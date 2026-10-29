"""Time the upstream MixedNet architecture on synthetic features, not a trained wake model."""
import argparse
import json
from pathlib import Path
import platform
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steps',type=int,default=50)
    parser.add_argument('--batch-size',type=int,default=32)
    parser.add_argument('--threads',type=int,default=4)
    parser.add_argument('--require-gpu',action='store_true',help='Fail instead of silently benchmarking CPU on the GPU machine')
    parser.add_argument('--output',type=Path,default=Path('results/cpu-benchmark.json'))
    args=parser.parse_args()
    if not 1<=args.steps<=200 or not 1<=args.batch_size<=128 or not 1<=args.threads<=8:
        parser.error('Use 1–200 steps, batch 1–128, and 1–8 threads')
    import tensorflow as tf
    import numpy as np
    from microwakeword import mixednet
    gpus=tf.config.list_physical_devices('GPU')
    if args.require_gpu and not gpus:
        raise RuntimeError('TensorFlow cannot see the NVIDIA GPU; fix GPU access before comparing timings')
    for gpu in gpus:tf.config.experimental.set_memory_growth(gpu,True)
    tf.config.threading.set_intra_op_parallelism_threads(args.threads)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.keras.utils.set_random_seed(42)
    architecture=argparse.ArgumentParser()
    mixednet.model_parameters(architecture)
    flags=architecture.parse_args(['--pointwise_filters','64,64,64,64',
        '--repeat_in_block','1,1,1,1','--mixconv_kernel_sizes','[5], [7,11], [9,15], [23]',
        '--residual_connection','0,0,0,0','--first_conv_filters','32',
        '--first_conv_kernel_size','5','--stride','3'])
    # Same 1.5 s receptive duration and 10 ms feature step as the upstream notebook.
    slices=1+(1500-30)//(10*flags.stride)+mixednet.spectrogram_slices_dropped(flags)
    model=mixednet.model(flags,(slices,40),args.batch_size)
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=.001),loss='binary_crossentropy')
    rng=np.random.default_rng(42)
    x=rng.normal(size=(args.batch_size,slices,40)).astype('float32')
    y=rng.integers(0,2,size=(args.batch_size,1)).astype('float32')
    # Graph compilation and allocator initialization are timed separately.
    start=time.perf_counter()
    for _ in range(3):model.train_on_batch(x,y)
    warmup=time.perf_counter()-start
    elapsed=[]
    for _ in range(args.steps):
        start=time.perf_counter();model.train_on_batch(x,y);elapsed.append(time.perf_counter()-start)
    mean=float(np.mean(elapsed))
    report=dict(purpose='Training compute benchmark using synthetic random features; not a Hey Adam model',
        device='GPU' if gpus else 'CPU',visible_gpus=[tf.config.experimental.get_device_details(g).get('device_name',g.name) for g in gpus],
        upstream_revision='4665173cd35f1cff9a61e06fc427f124766c488e',python=platform.python_version(),
        tensorflow=tf.__version__,parameters=model.count_params(),feature_shape=[slices,40],
        steps=args.steps,batch_size=args.batch_size,threads=args.threads,warmup_seconds=round(warmup,3),
        mean_step_ms=round(mean*1000,2),p95_step_ms=round(float(np.percentile(elapsed,95))*1000,2),
        projected_10000_steps_minutes_compute_only=round(mean*10000/60,1),
        excludes=['sample generation','augmentation','feature extraction','data loading','validation','export','quality tuning'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
