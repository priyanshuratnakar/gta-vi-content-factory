"""Background jobs with progress the web app can poll. One heavy job at a time keeps the Mac cool."""
import threading, time, uuid, traceback
from . import core

JOBS = {}
_queue_lock = threading.Lock()        # serialises misc jobs (covers, single re-renders)
dl_sem = threading.Semaphore(4)       # up to 4 parallel downloads at full line speed
clip_lock = threading.Lock()          # serialises GPU/CPU-heavy moment analysis & rendering

class Job:
    def __init__(self, kind, title):
        self.id = uuid.uuid4().hex[:10]
        self.kind, self.title = kind, title
        self.status, self.progress, self.step, self.error = "waiting", 0.0, "Waiting for the previous job", ""
        self.started = time.time()
        self.result = None

    def update(self, progress=None, step=None):
        if progress is not None: self.progress = max(0.0, min(1.0, progress))
        if step: self.step = step

    def as_dict(self):
        return {"id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
                "progress": round(self.progress, 3), "step": self.step, "error": self.error,
                "started": self.started, "result": self.result}

def start(kind, title, fn, *args, parallel=False, **kwargs):
    job = Job(kind, title)
    JOBS[job.id] = job

    def runner():
        ctx = threading.Lock() if parallel else _queue_lock
        with ctx:
            job.status, job.step = "running", "Starting"
            try:
                job.result = fn(job, *args, **kwargs)
                job.status, job.progress, job.step = "done", 1.0, "Done"
                core.say("job", f"{job.title}: done")
            except Exception as e:
                job.status, job.error = "failed", str(e)[:800]
                core.say("job", f"{job.title} failed: {e}", "error")
                core.say("job", traceback.format_exc()[-1500:], "debug")
    threading.Thread(target=runner, daemon=True).start()
    return job

def listing(limit=30):
    jobs = sorted(JOBS.values(), key=lambda j: j.started, reverse=True)[:limit]
    return [j.as_dict() for j in jobs]
