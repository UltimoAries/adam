"""Bounded recording persistence, independent of recognition and actions."""
from concurrent.futures import ThreadPoolExecutor
import threading

from .clips import ClipStorageFull
from .latency import mark


class RecordingWork:
    def __init__(self, archive, store, jobs):
        self.archive, self.store, self.jobs = archive, store, jobs
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='recordings')
        self.slots = threading.BoundedSemaphore(8)

    def reserve(self, entry):
        if not self.slots.acquire(blocking=False):
            entry.update(recording_status='skipped', archive_id=None, clip_id=None,
                         archive_warning='Recording queue is full. Voice processing continues.')
            return None
        return dict(lock=threading.Lock(), result=None, stored=False, released=False)

    def release(self, work):
        with work['lock']:
            if work['released']:
                return
            work['released'] = True
        self.slots.release()

    def completed(self, work, entry, result):
        if work is None:
            return
        with work['lock']:
            work['result'] = result
            stored = work['stored']
        if stored:
            self.pool.submit(self.finish, work, entry)

    def finish(self, work, entry):
        try:
            warning = self.archive.complete(entry.get('archive_id'), work['result'])
            if warning:
                entry['archive_warning'] = warning
        finally:
            self.release(work)

    def save(self, work, raw, name, entry, job_id):
        try:
            archived = self.archive.capture(raw, entry)
            entry.update(archive_id=archived['id'], archive_status=archived['status'],
                         archive_warning=archived['warning'])
            warning = self.archive.attach_job(archived['id'], job_id)
            if warning:
                entry['archive_warning'] = warning
            try:
                clip = self.store.add(raw, name, 'stream')
                entry['clip_id'] = clip['id']
                self.jobs.attach_clip(job_id, clip['id'])
            except ClipStorageFull:
                entry['recording_warning'] = 'Diagnostic clip not saved: the clip library is full. Voice processing continues.'
            except OSError:
                entry['recording_warning'] = 'Diagnostic clip not saved: storage is unavailable. Voice processing continues.'
        except Exception:
            entry['archive_warning'] = 'Recording persistence failed. Voice processing continues.'
        finally:
            mark(entry, 'recording_saved')
            entry['recording_status'] = 'finished'
            with work['lock']:
                work['stored'] = True
                complete = work['result'] is not None
            if complete:
                self.finish(work, entry)

    def close(self):
        # Called after recognition drains, so no further completions can arrive.
        self.pool.shutdown(wait=True)
