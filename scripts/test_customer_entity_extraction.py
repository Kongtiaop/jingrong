from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.customer_intent import CustomerIntentPlanner, extract_entities

def main():
    assert extract_entities('查询订单号 ORD-ABC-123 状态') == {'order_no':'ORD-ABC-123'}
    assert extract_entities('查看工单号 WO-20260808-001') == {'ticket_no':'WO-20260808-001'}
    assert extract_entities('查询产品名称：稳健增利现在是否在售')['product_name']=='稳健增利'
    plan=CustomerIntentPlanner().plan('查询订单号 ORD-ABC-123 的订单状态')
    task=next(x for x in plan.tasks if x.intent=='order_status')
    assert task.arguments['order_no']=='ORD-ABC-123'
    plan2=CustomerIntentPlanner().plan('查看工单号 WO-20260808-001 进度')
    task2=next(x for x in plan2.tasks if x.intent=='workorder_status')
    assert task2.arguments['ticket_no']=='WO-20260808-001'
    print({'order_entity':True,'ticket_entity':True,'arguments_propagated':True,'product_entity':True,'pass':True})
if __name__=='__main__':main()


