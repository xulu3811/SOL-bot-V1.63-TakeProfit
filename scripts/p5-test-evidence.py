"""Store exact synthetic test inputs/assertions and actual result per test."""
import hashlib
import inspect
import json
from pathlib import Path
import unittest
import sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'tests/unit'))
suite=unittest.TestLoader().discover(str(root/'tests/unit'),pattern='test_p5*.py')
records=[]
class Result(unittest.TextTestResult):
    def record(self,test,status,detail=''):
        method=getattr(type(test),test._testMethodName)
        source=inspect.getsource(method)
        records.append({'test':test.id(),'version':'guardian-p5-1','input_and_expected_assertions':source,'setup':inspect.getsource(type(test).setUp),'actual':status,'detail':detail,'source_sha256':hashlib.sha256(Path(inspect.getfile(method)).read_bytes()).hexdigest()})
    def addSuccess(self,test): super().addSuccess(test); self.record(test,'PASS')
    def addFailure(self,test,error): super().addFailure(test,error); self.record(test,'FAIL',self._exc_info_to_string(error,test))
    def addError(self,test,error): super().addError(test,error); self.record(test,'ERROR',self._exc_info_to_string(error,test))
result=unittest.TextTestRunner(resultclass=Result,verbosity=1).run(suite)
(root/'data/p5-test-evidence.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
raise SystemExit(0 if result.wasSuccessful() else 1)

