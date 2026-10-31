"""Bounded local Piper worker; the API never loads uploaded ONNX in its process."""
import json
import os
from pathlib import Path
import sys
import time


def memory_limit():
    if os.name!='nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(3*1024**3,3*1024**3))
        return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_=[('PerProcessUserTimeLimit',ctypes.c_longlong),('PerJobUserTimeLimit',ctypes.c_longlong),
            ('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),
            ('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_=[(name,ctypes.c_ulonglong) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
    class Extended(ctypes.Structure):
        _fields_=[('BasicLimitInformation',Basic),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),
            ('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    handle=kernel.CreateJobObjectW(None,None)
    limits=Extended();limits.BasicLimitInformation.LimitFlags=0x100;limits.ProcessMemoryLimit=1536*1024**2
    if not handle or not kernel.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle,kernel.GetCurrentProcess()):
        raise RuntimeError('Could not apply the voice worker memory bound')
    return handle  # Keep the job handle alive for the lifetime of this worker.


def inspect_model(path):
    import onnx
    model=onnx.load_model_from_string(Path(path).read_bytes())
    if any(item.domain not in ('','ai.onnx') for item in model.opset_import):raise ValueError('Custom ONNX operator domains are not supported')
    if model.functions or model.training_info:raise ValueError('ONNX local functions and training graphs are not supported in voice packages')
    def tensor(item):
        if item.data_location==onnx.TensorProto.EXTERNAL or item.external_data:raise ValueError('External ONNX tensor files are not supported')
    def graph(item):
        for value in item.initializer:tensor(value)
        for value in item.sparse_initializer:tensor(value.values);tensor(value.indices)
        for node in item.node:
            if node.domain not in ('','ai.onnx'):raise ValueError('Custom ONNX operators are not supported')
            for attribute in node.attribute:
                if attribute.type==onnx.AttributeProto.TENSOR:tensor(attribute.t)
                elif attribute.type==onnx.AttributeProto.TENSORS:
                    for value in attribute.tensors:tensor(value)
                elif attribute.type==onnx.AttributeProto.GRAPH:graph(attribute.g)
                elif attribute.type==onnx.AttributeProto.GRAPHS:
                    for value in attribute.graphs:graph(value)
                elif attribute.type==onnx.AttributeProto.SPARSE_TENSOR:
                    tensor(attribute.sparse_tensor.values);tensor(attribute.sparse_tensor.indices)
                elif attribute.type==onnx.AttributeProto.SPARSE_TENSORS:
                    for value in attribute.sparse_tensors:tensor(value.values);tensor(value.indices)
    graph(model.graph)
    return None


_loaded_key = None
_loaded_voice = None


def run(options):
    global _loaded_key, _loaded_voice
    from .voice_package import validate_files
    directory=Path(options['directory'])
    manifest=validate_files(directory)
    import numpy as np
    import onnxruntime as ort
    import av
    from piper import PiperVoice,SynthesisConfig
    from piper.config import PiperConfig
    import wave
    key=(str(directory.resolve()),manifest.model_dump_json())
    cached=key==_loaded_key
    if not cached:
        inspect_model(directory/'model.onnx')
        config=json.loads((directory/'model.onnx.json').read_bytes())
        session_options=ort.SessionOptions();session_options.intra_op_num_threads=2;session_options.inter_op_num_threads=1
        session_options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        session=ort.InferenceSession(str(directory/'model.onnx'),sess_options=session_options,providers=['CPUExecutionProvider'])
        inputs={item.name:item.type for item in session.get_inputs()}
        expected={'input':'tensor(int64)','input_lengths':'tensor(int64)','scales':'tensor(float)'}
        if any(inputs.get(name)!=dtype for name,dtype in expected.items()) or set(inputs)-set(expected)-{'sid'}:
            raise ValueError('ONNX inputs do not match the Piper VITS interface')
        if 'sid' in inputs and inputs['sid']!='tensor(int64)':raise ValueError('Invalid Piper speaker input')
        outputs=session.get_outputs()
        if len(outputs)!=1 or outputs[0].type!='tensor(float)' or len(outputs[0].shape) not in (3,4) or any(d!=1 for d in outputs[0].shape[1:-1]):
            raise ValueError('ONNX output must be one Piper float audio tensor with singleton channel dimensions')
        voice=PiperVoice(session=session,config=PiperConfig.from_dict(config))
        settings=manifest.settings
        synth=SynthesisConfig(speaker_id=settings.speaker_id,length_scale=settings.length_scale,
            noise_scale=settings.noise_scale,noise_w_scale=settings.noise_w_scale,normalize_audio=True)
        _loaded_voice=(voice,synth)
        _loaded_key=key
    voice,synth=_loaded_voice
    chunks=[];count=0;start=time.monotonic()
    resampler=av.AudioResampler(format='s16',layout='mono',rate=16000)
    for chunk in voice.synthesize(options['text'],syn_config=synth):
        data=np.frombuffer(chunk.audio_int16_bytes,dtype='<i2').reshape(1,-1)
        frame=av.AudioFrame.from_ndarray(data,format='s16',layout='mono');frame.sample_rate=chunk.sample_rate
        for output in resampler.resample(frame):
            values=output.to_ndarray().reshape(-1);count+=len(values)
            if count>240000:raise ValueError('Speech exceeds 15 seconds; shorten the text')
            chunks.append(values)
    chunks.extend(frame.to_ndarray().reshape(-1) for frame in resampler.resample(None))
    if not chunks:raise ValueError('Model did not produce audio')
    values=np.concatenate(chunks)
    if not 1600<=len(values)<=240000 or np.max(np.abs(values.astype(float)))<2:raise ValueError('Model output is silent or has an invalid duration')
    with wave.open(options['output'],'wb') as target:
        target.setparams((1,2,16000,0,'NONE','not compressed'));target.writeframes(values.tobytes())
    return dict(seconds=round(len(values)/16000,3),generation_ms=round((time.monotonic()-start)*1000),voice=manifest.key,model_cached=cached)


if __name__=='__main__':
    job=memory_limit()  # One bounded process, including while its model is idle.
    for line in sys.stdin.buffer:
        try:
            if len(line)>16384:raise ValueError('Voice request exceeds its bound')
            result=run(json.loads(line))
            print(json.dumps(dict(ok=True,**result)),flush=True)
        except Exception as exc:
            print(json.dumps(dict(ok=False,error=str(exc)[:400])),flush=True)
            raise SystemExit(1)
