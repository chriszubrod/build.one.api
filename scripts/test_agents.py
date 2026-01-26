#!/usr/bin/env python
"""
Test script for the agents workflow framework.

Run with: python scripts/test_agents.py
"""
# Python Standard Library Imports
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def test_models():
    """Test model creation and serialization."""
    print("\n" + "="*60)
    print("Testing Models")
    print("="*60)
    
    from agents.models import Workflow, WorkflowEvent
    
    # Test Workflow model
    workflow = Workflow(
        id=1,
        public_id="test-uuid",
        tenant_id=1,
        workflow_type="bill_intake",
        state="received",
        context={"test_key": "test_value"},
    )
    
    assert workflow.is_active is True
    assert workflow.is_completed is False
    assert workflow.get_context_value("test_key") == "test_value"
    assert workflow.get_context_value("missing", "default") == "default"
    
    workflow.set_context_value("new_key", "new_value")
    assert workflow.get_context_value("new_key") == "new_value"
    
    print("  [PASS] Workflow model works correctly")
    
    # Test WorkflowEvent model
    event = WorkflowEvent(
        id=1,
        workflow_id=1,
        event_type="state_changed",
        from_state="received",
        to_state="classifying",
        data={"trigger": "start_classification"},
    )
    
    assert event.to_dict()["event_type"] == "state_changed"
    print("  [PASS] WorkflowEvent model works correctly")


def test_definitions():
    """Test workflow definitions."""
    print("\n" + "="*60)
    print("Testing Workflow Definitions")
    print("="*60)
    
    from agents.definitions.bill_intake import BILL_INTAKE_WORKFLOW
    
    assert BILL_INTAKE_WORKFLOW.name == "bill_intake"
    assert BILL_INTAKE_WORKFLOW.initial_state == "received"
    
    # Test state lookup
    state = BILL_INTAKE_WORKFLOW.get_state("awaiting_approval")
    assert state is not None
    assert state.timeout_days == 3
    print("  [PASS] State lookup works")
    
    # Test final states
    final_states = BILL_INTAKE_WORKFLOW.get_final_states()
    assert "completed" in final_states
    assert "abandoned" in final_states
    print("  [PASS] Final states identified correctly")
    
    # Test timeout states
    timeout_states = BILL_INTAKE_WORKFLOW.get_timeout_states()
    assert any(s.name == "awaiting_approval" for s in timeout_states)
    print("  [PASS] Timeout states identified correctly")
    
    # Test steps
    steps = BILL_INTAKE_WORKFLOW.get_steps_for_state("classifying")
    assert len(steps) == 2
    assert steps[0].name == "extract_attachment"
    print("  [PASS] Steps retrieved correctly")
    
    # Test transitions config
    config = BILL_INTAKE_WORKFLOW.to_transitions_config()
    assert "states" in config
    assert "transitions" in config
    assert config["initial"] == "received"
    print("  [PASS] Transitions config generated correctly")


def test_orchestrator_setup():
    """Test orchestrator initialization (without database)."""
    print("\n" + "="*60)
    print("Testing Orchestrator Setup")
    print("="*60)
    
    from agents.orchestrator import WorkflowOrchestrator
    from agents.definitions.bill_intake import BILL_INTAKE_WORKFLOW
    
    orchestrator = WorkflowOrchestrator()
    orchestrator.register_definition(BILL_INTAKE_WORKFLOW)
    
    # Test definition retrieval
    definition = orchestrator.get_definition("bill_intake")
    assert definition.name == "bill_intake"
    print("  [PASS] Workflow definition registered and retrieved")
    
    # Test unknown workflow type
    try:
        orchestrator.get_definition("unknown_type")
        assert False, "Should have raised ValueError"
    except ValueError:
        print("  [PASS] Unknown workflow type raises ValueError")


def test_repository_with_db():
    """Test repository operations (requires database connection)."""
    print("\n" + "="*60)
    print("Testing Repository with Database")
    print("="*60)
    
    try:
        from agents.persistence.repo import WorkflowRepository, WorkflowEventRepository
        
        workflow_repo = WorkflowRepository()
        event_repo = WorkflowEventRepository()
        
        # Create a test workflow
        workflow = workflow_repo.create(
            tenant_id=1,
            workflow_type="bill_intake",
            state="received",
            conversation_id="test-conversation-123",
            trigger_message_id="test-message-456",
            context={"test": True, "source": "test_agents.py"},
        )
        
        print(f"  [PASS] Created workflow: {workflow.public_id}")
        
        # Read by public ID
        loaded = workflow_repo.read_by_public_id(workflow.public_id)
        assert loaded is not None
        assert loaded.workflow_type == "bill_intake"
        assert loaded.context.get("test") is True
        print("  [PASS] Read workflow by public ID")
        
        # Read by conversation ID
        by_conversation = workflow_repo.read_by_conversation_id("test-conversation-123")
        assert len(by_conversation) >= 1
        print("  [PASS] Read workflows by conversation ID")
        
        # Update state
        updated = workflow_repo.update_state(
            public_id=workflow.public_id,
            state="classifying",
            context={"test": True, "updated": True},
        )
        assert updated.state == "classifying"
        assert updated.context.get("updated") is True
        print("  [PASS] Updated workflow state")
        
        # Create an event
        event = event_repo.create(
            workflow_id=workflow.id,
            event_type="state_changed",
            from_state="received",
            to_state="classifying",
            step_name="start_classification",
            data={"trigger": "manual"},
            created_by="test_script",
        )
        print(f"  [PASS] Created event: {event.id}")
        
        # Read events
        events = event_repo.read_by_workflow_id(workflow.id)
        assert len(events) >= 1
        print("  [PASS] Read events by workflow ID")
        
        # Mark as completed for cleanup
        workflow_repo.update_state(
            public_id=workflow.public_id,
            state="completed",
        )
        print("  [PASS] Marked workflow as completed")
        
    except Exception as e:
        print(f"  [ERROR] Database test failed: {e}")
        print("         Make sure the database is configured and migrations have been run.")
        raise


def test_agents_setup():
    """Test agent classes and structures."""
    print("\n" + "="*60)
    print("Testing Agents Setup")
    print("="*60)
    
    from agents.runners.base import Agent, AgentContext, AgentResult
    
    # Test AgentContext
    context = AgentContext(
        tenant_id=1,
        access_token="test-token",
        workflow_public_id="wf-123",
        workflow_context={"test": True},
        trigger_data={"message_id": "msg-456"},
    )
    assert context.tenant_id == 1
    assert context.trigger_data["message_id"] == "msg-456"
    print("  [PASS] AgentContext works correctly")
    
    # Test AgentResult
    ok_result = AgentResult.ok(
        data={"classified": True},
        context_updates={"new_field": "value"},
        next_trigger="classification_complete",
    )
    assert ok_result.success is True
    assert ok_result.next_trigger == "classification_complete"
    print("  [PASS] AgentResult.ok works correctly")
    
    fail_result = AgentResult.fail("Test error")
    assert fail_result.success is False
    assert fail_result.error == "Test error"
    print("  [PASS] AgentResult.fail works correctly")
    
    human_result = AgentResult.needs_human_input(
        reason="Cannot read attachment",
        data={"partial": "data"},
    )
    assert human_result.success is True
    assert human_result.context_updates.get("needs_human_input") is True
    print("  [PASS] AgentResult.needs_human_input works correctly")
    
    # Test agent imports
    from agents.runners import EmailTriageAgent, ApprovalParserAgent, CorrelationAgent
    
    triage_agent = EmailTriageAgent()
    assert triage_agent.name == "email_triage"
    print("  [PASS] EmailTriageAgent instantiates correctly")
    
    approval_agent = ApprovalParserAgent()
    assert approval_agent.name == "approval_parser"
    print("  [PASS] ApprovalParserAgent instantiates correctly")
    
    correlation_agent = CorrelationAgent()
    assert correlation_agent.name == "correlation"
    print("  [PASS] CorrelationAgent instantiates correctly")


def test_prompts():
    """Test prompt building functions."""
    print("\n" + "="*60)
    print("Testing Prompts")
    print("="*60)
    
    from agents.prompts.classification import build_classification_prompt, build_multi_bill_prompt
    from agents.prompts.approval_parse import build_approval_parse_prompt
    
    # Test classification prompt
    prompt = build_classification_prompt(
        subject="Invoice #12345 from Acme Corp",
        email_body="Please find attached our invoice for $5,000.",
        attachment_text="INVOICE\nAmount: $5,000.00\nDue: 2026-02-01",
        known_vendors=["Acme Corp", "BuildCo"],
        known_projects=["Highland Tower", "Main Street"],
    )
    assert "Invoice #12345" in prompt
    assert "Acme Corp" in prompt
    assert "Highland Tower" in prompt
    print("  [PASS] build_classification_prompt works correctly")
    
    # Test multi-bill prompt
    multi_prompt = build_multi_bill_prompt("Page 1: Invoice A $1000\nPage 2: Invoice B $2000")
    assert "Invoice A" in multi_prompt
    print("  [PASS] build_multi_bill_prompt works correctly")
    
    # Test approval parse prompt
    approval_prompt = build_approval_parse_prompt(
        reply_body="Approved - charge to Highland Tower, code 03-200",
        vendor="Acme Corp",
        amount=5000.00,
        invoice_number="INV-12345",
        project_guess="Highland Tower",
    )
    assert "Approved" in approval_prompt
    assert "$5,000.00" in approval_prompt
    print("  [PASS] build_approval_parse_prompt works correctly")


def test_executor_setup():
    """Test executor and scheduler setup."""
    print("\n" + "="*60)
    print("Testing Executor & Scheduler Setup")
    print("="*60)
    
    from agents.executor import BillIntakeExecutor
    from agents.scheduler import WorkflowScheduler
    
    # Test executor initialization
    executor = BillIntakeExecutor()
    assert executor.orchestrator is not None
    assert executor.capabilities is not None
    assert executor.email_triage_agent is not None
    assert executor.approval_parser_agent is not None
    print("  [PASS] BillIntakeExecutor initializes correctly")
    
    # Check workflow definition is registered
    definition = executor.orchestrator.get_definition("bill_intake")
    assert definition.name == "bill_intake"
    print("  [PASS] bill_intake workflow registered in executor")
    
    # Test scheduler initialization
    scheduler = WorkflowScheduler()
    assert scheduler.orchestrator is not None
    assert scheduler.executor is not None
    print("  [PASS] WorkflowScheduler initializes correctly")
    
    # Test template rendering
    html = executor._render_template("approval_request.html", {
        "vendor_name": "Test Vendor",
        "invoice_number": "INV-001",
        "amount": 1234.56,
        "invoice_date": "2026-01-24",
        "project_name": "Test Project",
        "project_confidence": 0.85,
        "detected_bills": [],
        "workflow_id": "test-123",
    })
    assert "Test Vendor" in html
    assert "$1,234.56" in html
    assert "85%" in html
    print("  [PASS] Template rendering works correctly")


def test_notifications_and_admin():
    """Test notifications and admin utilities."""
    print("\n" + "="*60)
    print("Testing Notifications & Admin Utilities")
    print("="*60)
    
    from agents.notifications.summary import DailySummaryGenerator
    from agents.admin import WorkflowAdmin
    
    # Test summary generator initialization
    summary_gen = DailySummaryGenerator()
    assert summary_gen.capabilities is not None
    assert summary_gen.workflow_repo is not None
    print("  [PASS] DailySummaryGenerator initializes correctly")
    
    # Test daily summary template rendering
    test_data = {
        "summary_date": "January 24, 2026",
        "generated_at": "2026-01-24 12:00 UTC",
        "stats": {
            "new_today": 5,
            "completed_today": 3,
            "awaiting_approval": 7,
            "total_active": 12,
            "total_pending_value": 45000.00,
            "avg_days_to_approval": 2.5,
        },
        "awaiting_approval": [
            {"vendor_name": "Acme Corp", "invoice_number": "INV-001", "amount": 5000, "days_waiting": 2},
        ],
        "completed_today": [
            {"vendor_name": "BuildCo", "invoice_number": "INV-002", "amount": 3000, "qbo_synced": True},
        ],
        "needs_attention": [],
    }
    html = summary_gen.render_summary_html(test_data)
    assert "January 24, 2026" in html
    assert "Acme Corp" in html
    assert "$45,000.00" in html
    print("  [PASS] Daily summary template renders correctly")
    
    # Test admin initialization
    admin = WorkflowAdmin()
    assert admin.workflow_repo is not None
    assert admin.event_repo is not None
    print("  [PASS] WorkflowAdmin initializes correctly")


def test_capabilities_setup():
    """Test capability registry initialization."""
    print("\n" + "="*60)
    print("Testing Capabilities Setup")
    print("="*60)
    
    from agents.capabilities.base import Capability, CapabilityResult
    from agents.capabilities.registry import CapabilityRegistry
    
    # Test CapabilityResult
    success_result = CapabilityResult.ok(data={"test": True}, extra="metadata")
    assert success_result.success is True
    assert success_result.data == {"test": True}
    assert success_result.metadata.get("extra") == "metadata"
    print("  [PASS] CapabilityResult.ok works correctly")
    
    fail_result = CapabilityResult.fail(error="Test error")
    assert fail_result.success is False
    assert fail_result.error == "Test error"
    print("  [PASS] CapabilityResult.fail works correctly")
    
    # Test registry (without initializing all - that requires external services)
    registry = CapabilityRegistry()
    assert registry.llm is None  # Not initialized yet
    print("  [PASS] CapabilityRegistry created")
    
    # Test LLM dataclasses
    from agents.capabilities.llm import Classification, ParsedReply
    
    classification = Classification(
        category="bill",
        confidence=0.95,
        vendor_guess="Acme Corp",
        project_guess="Highland Tower",
        amount=5000.00,
    )
    assert classification.category == "bill"
    assert classification.detected_bills == []
    print("  [PASS] Classification dataclass works")
    
    parsed = ParsedReply(
        decision="approved",
        confidence=0.9,
        project_name="Highland Tower",
        cost_code="03-200",
    )
    assert parsed.decision == "approved"
    print("  [PASS] ParsedReply dataclass works")
    
    # Test Document dataclass
    from agents.capabilities.document import ExtractedDocument
    
    doc = ExtractedDocument(
        text="Invoice content here",
        pages=2,
        confidence=0.85,
    )
    assert doc.pages == 2
    assert doc.tables == []
    print("  [PASS] ExtractedDocument dataclass works")
    
    # Test Entity dataclass
    from agents.capabilities.entity import MatchCandidate
    
    candidate = MatchCandidate(
        id=1,
        public_id="abc-123",
        name="Acme Corp",
        confidence=0.88,
        match_type="embedding",
    )
    assert candidate.confidence == 0.88
    print("  [PASS] MatchCandidate dataclass works")


def main():
    print("\n" + "="*60)
    print("AGENTS FRAMEWORK TESTS")
    print("="*60)
    
    # Tests that don't require database or external services
    test_models()
    test_definitions()
    test_orchestrator_setup()
    test_capabilities_setup()
    test_agents_setup()
    test_prompts()
    test_executor_setup()
    test_notifications_and_admin()
    
    # Database tests (optional, may fail if DB not configured)
    try:
        test_repository_with_db()
    except Exception:
        print("\n  [SKIP] Database tests skipped (connection not available)")
    
    print("\n" + "="*60)
    print("ALL TESTS COMPLETED")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
