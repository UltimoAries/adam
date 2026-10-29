"""Build a data-only v1 Piper voice ZIP; the head-end probes it on import."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
from headend.voice_package import MAX_PACKAGE,PROBE_TEXT,digest,template,validate_files


def build(options):
    output=Path(options.output).resolve()
    if output.exists():raise ValueError('Output already exists; choose a new filename or remove it explicitly')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='voice-package-',dir=output.parent) as work:
        work=Path(work);stage=work/'files';stage.mkdir()
        model=Path(options.model)
        sources={'model.onnx':model,'model.onnx.json':Path(options.config or str(model)+'.json'),
                 'LICENSE.txt':Path(options.license_file)}
        if options.model_card:sources['MODEL_CARD.md']=Path(options.model_card)
        for name,path in sources.items():
            if not path.is_file() or path.is_symlink():raise ValueError('Use a regular input file: '+str(path))
            shutil.copyfile(path,stage/name)
        manifest=template()
        manifest.update(id=options.id,version=options.version,name=options.name,description=options.description,
                        author=options.author,language=options.language)
        manifest['model']['sha256']=digest(stage/'model.onnx')
        manifest['config']['sha256']=digest(stage/'model.onnx.json')
        manifest['license'].update(name=options.license_name,attribution=options.attribution,source_url=options.source_url)
        manifest['settings'].update(speaker_id=options.speaker_id,length_scale=options.length_scale,
                                   noise_scale=options.noise_scale,noise_w_scale=options.noise_w_scale)
        (stage/'voice.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
        validated=validate_files(stage)
        probe=None
        if options.probe:
            from headend.voices import PiperRuntime
            audio,probe=PiperRuntime(work/'runtime').synthesize(stage,PROBE_TEXT)
        archive=work/'voice.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as target:
            for path in sorted(stage.iterdir()):target.write(path,path.name)
        if archive.stat().st_size>MAX_PACKAGE:raise ValueError('Voice ZIP exceeds 160 MiB')
        # Refuse to overwrite a file created while packaging was in progress.
        with output.open('xb') as target,archive.open('rb') as source:shutil.copyfileobj(source,target)
        return dict(voice_id=validated.key,output=str(output),bytes=output.stat().st_size,probe=probe)


def parser():
    args=argparse.ArgumentParser(description=__doc__)
    args.add_argument('--model',required=True,help='Exported Piper .onnx file')
    args.add_argument('--config',help='Matching JSON; defaults to MODEL.json')
    args.add_argument('--id',required=True,help='Stable lowercase ID, e.g. adam-character')
    args.add_argument('--version',default='1.0.0')
    args.add_argument('--name',required=True)
    args.add_argument('--description',default='')
    args.add_argument('--author',required=True)
    args.add_argument('--language',default='en-US',choices=['en-US','en-GB','en-AU','en-CA'])
    args.add_argument('--license-file',required=True)
    args.add_argument('--license-name',required=True)
    args.add_argument('--attribution',required=True)
    args.add_argument('--source-url',default='')
    args.add_argument('--model-card')
    args.add_argument('--speaker-id',type=int,default=0)
    args.add_argument('--length-scale',type=float,default=1)
    args.add_argument('--noise-scale',type=float,default=.667)
    args.add_argument('--noise-w-scale',type=float,default=.8)
    args.add_argument('--probe',action='store_true',help='Also test actual local synthesis before writing the ZIP')
    args.add_argument('--output',required=True)
    return args


if __name__=='__main__':
    args=parser()
    try:print(json.dumps(build(args.parse_args()),indent=2))
    except (ValueError,OSError) as exc:args.exit(1,'Package failed: '+str(exc)+'\n')
