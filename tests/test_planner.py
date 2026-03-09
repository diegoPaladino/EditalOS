from editalos.schemas import TopicPriority
from editalos.services.planner import PlannerService


def test_rebalance_minutes_matches_target():
    items = [
        TopicPriority(topic_id=1, subject_name="A", topic_name="X", priority_score=3.0, recommended_minutes=100, rationale={}),
        TopicPriority(topic_id=2, subject_name="B", topic_name="Y", priority_score=2.0, recommended_minutes=80, rationale={}),
        TopicPriority(topic_id=3, subject_name="C", topic_name="Z", priority_score=1.0, recommended_minutes=60, rationale={}),
    ]
    result = PlannerService._rebalance_minutes(items, total_minutes=240)
    assert sum(item.recommended_minutes for item in result) == 240
    assert all(item.recommended_minutes >= 20 for item in result)
