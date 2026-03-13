import threading
import time
from abc import ABC, abstractmethod


class BaseIngestor(ABC):
    
    def __init__(self):
        self._running = False
        self._thread = None
    
    @abstractmethod
    def run(self):
        """Main loop - implement in subclass"""
        pass
    
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_wrapper, daemon=True)
        self._thread.start()
    
    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
    
    def _run_wrapper(self):
        while self._running:
            try:
                self.run()
            except Exception as e:
                print(f"[{self.__class__.__name__}] Error: {e}")
                time.sleep(5)
