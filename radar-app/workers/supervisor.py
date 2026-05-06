import threading
import time
import os
import logging
from enum import Enum
from typing import Dict, Type, Optional, Any
from Classes.Ingestors.BaseIngestor import BaseIngestor

logger = logging.getLogger("SUPERVISOR")

class WorkerStatus(Enum):
    DISABLED = "DISABLED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    STOPPED = "STOPPED"

class ManagedWorker:
    def __init__(self, name: str, worker_class: Type[BaseIngestor], enabled: bool = False, health_window: int = 60):
        self.name = name
        self.worker_class = worker_class
        self.enabled = enabled
        self.instance: Optional[BaseIngestor] = None
        self.status = WorkerStatus.DISABLED if not enabled else WorkerStatus.STOPPED
        self.health_window = health_window
        
        # Performance/Health Metrics
        self.last_started: Optional[float] = None
        self.last_stopped: Optional[float] = None
        self.last_attempt_at: Optional[float] = None
        self.last_success_at: Optional[float] = None
        self.last_publish_at: Optional[float] = None
        self.last_message_at: Optional[float] = None
        self.last_batch_size: int = 0
        self.total_batches: int = 0
        self.total_aircraft_seen: int = 0
        self.total_errors: int = 0
        self.last_http_status: Optional[int] = None
        self.restart_count = 0
        self.last_error: Optional[str] = None
        
        self.stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if not self.enabled:
            return
        
        self.stop_event.clear()
        self.status = WorkerStatus.STARTING
        self._thread = threading.Thread(target=self._run_loop, name=f"Worker-{self.name}", daemon=True)
        self._thread.start()

    def stop(self):
        self.stop_event.set()
        if self.instance:
            try:
                self.instance.stop()
            except Exception as e:
                logger.error(f"[WORKER:{self.name}] Error during stop: {e}")
        
        if self._thread:
            self._thread.join(timeout=5)
        
        self.status = WorkerStatus.STOPPED
        self.last_stopped = time.time()

    def update_metrics(self, **kwargs):
        """Allow the worker to report its progress"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        
        # If we got a success or a message, we might be healthy again
        if kwargs.get("last_success_at") or kwargs.get("last_message_at"):
            if self.status == WorkerStatus.DEGRADED:
                self.status = WorkerStatus.RUNNING

    def _run_loop(self):
        backoff = 1.0
        while not self.stop_event.is_set():
            self.last_started = time.time()
            self.status = WorkerStatus.RUNNING
            logger.info(f"[WORKER:{self.name}] Started (internal mode)")
            
            try:
                self.instance = self.worker_class()
                # Inject supervisor reference for metric reporting if worker supports it
                if hasattr(self.instance, "set_supervisor"):
                    self.instance.set_supervisor(self)
                
                self.instance._running = True 
                self.instance.run()
                
                if not self.stop_event.is_set():
                    logger.warning(f"[WORKER:{self.name}] Unexpected exit")
                    self.status = WorkerStatus.ERROR
                    self.last_error = "Unexpected exit"
            except Exception as e:
                self.last_error = str(e)
                self.status = WorkerStatus.ERROR
                self.total_errors += 1
                logger.error(f"[WORKER:{self.name}] Crash: {e}")
            
            if self.stop_event.is_set():
                break
                
            self.restart_count += 1
            logger.info(f"[WORKER:{self.name}] Restarting in {backoff}s... (count: {self.restart_count})")
            
            stop_time = time.time() + backoff
            while time.time() < stop_time and not self.stop_event.is_set():
                time.sleep(0.5)
            
            backoff = min(backoff * 2, 60.0)

    def get_current_status(self) -> WorkerStatus:
        if not self.enabled:
            return WorkerStatus.DISABLED
        if self.status in (WorkerStatus.STOPPED, WorkerStatus.ERROR):
            return self.status
            
        # Check health window
        now = time.time()
        # If we haven't had a success in the health window, we are degraded
        # (Only if we've been running long enough)
        if self.last_started and (now - self.last_started > self.health_window):
            last_activity = self.last_success_at or self.last_started
            if now - last_activity > self.health_window:
                return WorkerStatus.DEGRADED
                
        return WorkerStatus.RUNNING

    def to_dict(self):
        current_status = self.get_current_status()
        
        return {
            "enabled": self.enabled,
            "status": current_status.value,
            "metrics": {
                "last_started": self.last_started,
                "last_stopped": self.last_stopped,
                "last_attempt_at": self.last_attempt_at,
                "last_success_at": self.last_success_at,
                "last_publish_at": self.last_publish_at,
                "last_message_at": self.last_message_at,
                "last_batch_size": self.last_batch_size,
                "total_batches": self.total_batches,
                "total_aircraft_seen": self.total_aircraft_seen,
                "total_errors": self.total_errors,
                "last_http_status": self.last_http_status,
                "restart_count": self.restart_count,
            },
            "last_error": self.last_error
        }

class WorkerSupervisor:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(WorkerSupervisor, cls).__new__(cls)
            cls._instance.workers: Dict[str, ManagedWorker] = {}
        return cls._instance

    def register_worker(self, name: str, worker_class: Type[BaseIngestor], env_var: str, health_window: int = 60):
        enabled = os.getenv(env_var, "false").lower() == "true"
        if os.getenv("ENABLE_INTERNAL_WORKERS", "false").lower() != "true":
            enabled = False
            
        self.workers[name] = ManagedWorker(name, worker_class, enabled, health_window=health_window)
        logger.info(f"[SUPERVISOR] Registered {name} (enabled: {enabled})")

    def start_all(self):
        logger.info("[SUPERVISOR] Starting all enabled workers...")
        for worker in self.workers.values():
            if worker.enabled:
                worker.start()

    def stop_all(self):
        logger.info("[SUPERVISOR] Stopping all workers...")
        for worker in self.workers.values():
            worker.stop()

    def get_status(self):
        workers_data = {name: w.to_dict() for name, w in self.workers.items()}
        
        enabled_count = sum(1 for w in self.workers.values() if w.enabled)
        running_count = sum(1 for w in workers_data.values() if w["status"] == "RUNNING")
        degraded_count = sum(1 for w in workers_data.values() if w["status"] == "DEGRADED")
        error_count = sum(1 for w in workers_data.values() if w["status"] == "ERROR")
        
        status = "healthy"
        if error_count > 0: status = "error"
        elif degraded_count > 0: status = "degraded"
        elif enabled_count > 0 and running_count == 0: status = "error"

        return {
            "status": status,
            "total_workers": len(self.workers),
            "enabled_workers": enabled_count,
            "running_workers": running_count,
            "degraded_workers": degraded_count,
            "error_workers": error_count,
            "workers": workers_data
        }

_supervisor = None

def getSupervisor() -> WorkerSupervisor:
    global _supervisor
    if _supervisor is None:
        _supervisor = WorkerSupervisor()
    return _supervisor
