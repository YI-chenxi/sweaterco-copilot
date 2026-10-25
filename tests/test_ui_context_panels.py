"""UI contract tests for context cards, without a Streamlit dependency."""
from app.agent.contracts import AgentReply
from app.agent.orchestrator import AgentOrchestrator
from app.ui.context_panels import contextual_sections, reply_headline
from app.ui.view_models import guided_decision


def _reply(flow, **values):
    return AgentOrchestrator().handle(guided_decision(flow, **values))


def test_order_details_derived_from_backend_only():
    r = _reply('Order status', order_id='ORD-057')
    sections = contextual_sections(r)
    assert len(sections) == 1
    assert sections[0]['title'].endswith('Order details')
    assert ('Order ID', 'ORD-057') in sections[0]['facts']
    assert 'completion' in sections[0]['description']


def test_production_historical_reference():
    r = _reply('Production health', stage='ASSEMBLY')
    assert reply_headline(r)[0] == 'ASSEMBLY · ABNORMAL'
    sections = contextual_sections(r)
    assert len(sections) == 1
    assert 'Historical reference' in sections[0]['title']
    assert any(k == 'Deviation (%)' for k, _ in sections[0]['facts'])


def test_feasibility_context_has_only_real_supported_data():
    r = _reply('Order feasibility', category='TOPS', pieces=800,
               requested_delivery_date='2026-04-25')
    labels = [p['title'] for p in contextual_sections(r)]
    assert any('Capacity analysis' in s for s in labels)
    assert any('External workshops' in s for s in labels)
    assert not any('Material availability' in s for s in labels)
    assert not any('Shipment' in s for s in labels)
    assert reply_headline(r)[0].endswith('CONDITIONAL')


def test_empty_failure_not_a_success_badge():
    reply = AgentReply(text='Not found', metadata={'status':'empty','intent':'order_status'})
    assert reply_headline(reply) == ('No verified result', 'caution')
    assert contextual_sections(reply) == []


def test_briefing_has_no_fake_extra_panels():
    r = _reply('Morning briefing', top_n=3)
    assert contextual_sections(r) == []
    assert '3 priority findings' in reply_headline(r)[0]


def test_no_structured_data_is_safe():
    reply = AgentReply(text='Unknown request', needs_clarification=True,
                       metadata={'intent':'unknown'})
    assert reply_headline(reply)[0] == 'More information needed'
    assert contextual_sections(reply) == []
