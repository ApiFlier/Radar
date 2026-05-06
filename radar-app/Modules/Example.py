import time
from Classes.ApiBase import ApiBase

class Example(ApiBase):
    def execute(self):
        self.responseData = {"ok": True}
        self.sendResponse(self.SUCCESS)

    def requiresGet(self):
        return True
