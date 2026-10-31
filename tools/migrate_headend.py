"""Offline portable data/model backup. Uses only the Python standard library."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import socket
import sqlite3
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def require_stopped():
    try:
        # Windows can take about two seconds to refuse a closed loopback port.
        connection = socket.create_connection(('127.0.0.1', 8765), timeout=5)
    except ConnectionRefusedError:
        return
    except OSError as exc:
        raise ValueError('Cannot verify that the local headend is stopped') from exc
    connection.close()
    raise ValueError('Stop the local headend on port 8765 before exporting or restoring')


def export_bundle(data, models, output):
    data, models, output = Path(data).resolve(), Path(models).resolve(), Path(output).resolve()
    if not (data/'fleet.sqlite3').is_file() or not (models/'stt'/'base.en'/'model.bin').is_file():
        raise ValueError('Source must contain the fleet database and installed base.en model')
    if output.is_relative_to(data) or output.is_relative_to(models):
        raise ValueError('Write the backup outside the runtime folders')
    manifest = {}
    # Exclusive create prevents silently overwriting a previous backup.
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for label, root in [('data', data), ('models', models)]:
            for source in sorted(root.rglob('*')):
                if source.is_symlink() or not source.resolve().is_relative_to(root):
                    raise ValueError('Migration does not follow links outside runtime folders')
                if not source.is_file():
                    continue
                relative = source.relative_to(root)
                if (source.suffix in ('.log', '.pid', '.tmp') or
                        (source.suffix=='.part' and relative.parts[0]!='training_archive') or
                        source.name.endswith(('-wal', '-shm', '-journal')) or
                        (label == 'data' and (relative.parts[0] in ('tts','deployment') or
                         relative.parts[:2] in (('voices', 'work'), ('voices', 'uploads'))))):
                    continue
                name = label+'/'+relative.as_posix()
                digest = hashlib.sha256()
                with tempfile.TemporaryDirectory(prefix='adam-snapshot-') as temporary:
                    selected = source
                    if source.suffix == '.sqlite3':
                        selected = Path(temporary)/source.name
                        # The stopped source may have a hot rollback journal after
                        # a crash. Allow SQLite to recover it before taking a backup;
                        # mode=rw still refuses to create a missing source database.
                        original = sqlite3.connect(source.as_uri()+'?mode=rw', uri=True)
                        target = sqlite3.connect(selected)
                        try:
                            original.backup(target)
                            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                                raise ValueError('Database integrity check failed')
                        finally:
                            original.close(); target.close()
                    with selected.open('rb') as reader, archive.open(name, 'w', force_zip64=True) as writer:
                        for chunk in iter(lambda: reader.read(1024*1024), b''):
                            digest.update(chunk); writer.write(chunk)
                manifest[name] = digest.hexdigest()
        archive.writestr('manifest.json', json.dumps(dict(format=1, files=manifest), indent=2))
    return len(manifest)


def restore_bundle(archive_path, destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any((destination/name).exists() for name in ('data', 'models')):
        raise ValueError('Restore into a headend folder with no data or models directories')
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or 'manifest.json' not in names:
            raise ValueError('Invalid migration archive')
        if archive.getinfo('manifest.json').file_size > 16*1024*1024:
            raise ValueError('Migration manifest is too large')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('format') != 1 or set(manifest.get('files', {})) != set(names)-{'manifest.json'}:
            raise ValueError('Migration manifest does not match archive')
        if sum(item.file_size for item in archive.infolist()) > 32*1024**3:
            raise ValueError('Migration exceeds the 32 GiB limit')
        with tempfile.TemporaryDirectory(prefix='.adam-restore-', dir=destination) as temporary:
            staging = Path(temporary).resolve()
            assert staging.is_relative_to(destination)
            for name, expected in manifest['files'].items():
                path = PurePosixPath(name)
                if (path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or
                        len(path.parts) < 2 or path.parts[0] not in ('data', 'models')):
                    raise ValueError('Unsafe archive path')
                output = staging.joinpath(*path.parts)
                if not output.resolve().is_relative_to(staging):
                    raise ValueError('Unsafe archive path')
                output.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(name) as reader, output.open('xb') as writer:
                    for chunk in iter(lambda: reader.read(1024*1024), b''):
                        digest.update(chunk); writer.write(chunk)
                if digest.hexdigest() != expected:
                    raise ValueError('Migration checksum mismatch: '+name)
            if not (staging/'data'/'fleet.sqlite3').is_file() or not (staging/'models'/'stt'/'base.en'/'model.bin').is_file():
                raise ValueError('Migration is missing the database or local recognition model')
            # All files are validated before installing either runtime directory.
            for name in ('data', 'models'):
                (staging/name).rename(destination/name)
    return len(manifest['files'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export')
    export.add_argument('--data', type=Path, default=ROOT/'headend'/'data')
    export.add_argument('--models', type=Path, default=ROOT/'headend'/'models')
    export.add_argument('--output', type=Path, required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('archive', type=Path)
    restore.add_argument('--destination', type=Path, default=ROOT/'headend')
    args = parser.parse_args()
    require_stopped()
    count = (export_bundle(args.data, args.models, args.output) if args.command == 'export'
             else restore_bundle(args.archive, args.destination))
    print(f'{args.command.capitalize()} complete: {count} files verified. The archive contains private runtime data.')


if __name__ == '__main__':
    main()
