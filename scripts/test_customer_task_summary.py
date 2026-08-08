from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.customer_task_executor import CustomerTaskExecutor, TaskExecutionResult

def main():
    s=CustomerTaskExecutor.summarize
    assert s([TaskExecutionResult('1','order_status','succeeded',answer='订单完成')]) == ('completed', None, '订单完成')
    status, clarification, answer=s([TaskExecutionResult('1','order_status','succeeded',answer='订单完成'),TaskExecutionResult('2','asset_summary','needs_clarification',clarification='请补充账户')])
    assert status=='partially_completed' and clarification=='请补充账户' and '订单完成' in answer
    assert s([TaskExecutionResult('1','unknown','needs_clarification',clarification='请说明')])[0]=='needs_clarification'
    assert s([TaskExecutionResult('1','x','failed',error='db')])[0]=='failed'
    assert s([], requires_handoff=True)[0]=='handoff'
    print({'completed':True,'partial_success':True,'clarification':True,'failed':True,'handoff':True,'pass':True})
if __name__=='__main__': main()

