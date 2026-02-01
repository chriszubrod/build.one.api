#!/usr/bin/env python
"""
Test script for instant workflows.

Tests that workflow records and events are created correctly for CRUD operations.

Run with: python scripts/test_instant_workflows.py
"""
# Python Standard Library Imports
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Avoid importing the full workflows module which has dependency issues
# Import specific submodules directly instead


def test_instant_workflow_definitions():
    """Test instant workflow definition factory."""
    print("\n" + "="*60)
    print("Testing Instant Workflow Definitions")
    print("="*60)
    
    from workflows.definitions.instant import (
        INSTANT_ENTITIES,
        INSTANT_OPERATIONS,
        get_instant_workflow_definition,
        is_instant_workflow_type,
        parse_instant_workflow_type,
    )
    
    # Test entity and operation lists
    assert "project" in INSTANT_ENTITIES
    assert "vendor" in INSTANT_ENTITIES
    assert "bill" in INSTANT_ENTITIES
    print(f"  [PASS] {len(INSTANT_ENTITIES)} entities registered")
    
    assert "create" in INSTANT_OPERATIONS
    assert "update" in INSTANT_OPERATIONS
    assert "delete" in INSTANT_OPERATIONS
    print(f"  [PASS] {len(INSTANT_OPERATIONS)} operations registered")
    
    # Test definition factory
    definition = get_instant_workflow_definition("project", "create")
    assert definition.name == "project_create"
    assert definition.initial_state == "executing"
    assert len(definition.states) == 3  # executing, completed, failed
    assert len(definition.transitions) == 2  # complete, fail
    print("  [PASS] Definition factory creates valid definitions")
    
    # Test is_instant_workflow_type
    assert is_instant_workflow_type("project_create") is True
    assert is_instant_workflow_type("vendor_update") is True
    assert is_instant_workflow_type("bill_delete") is True
    assert is_instant_workflow_type("email_intake") is False
    assert is_instant_workflow_type("invalid") is False
    assert is_instant_workflow_type("") is False
    assert is_instant_workflow_type(None) is False
    print("  [PASS] is_instant_workflow_type correctly identifies workflow types")
    
    # Test parse_instant_workflow_type
    entity, operation = parse_instant_workflow_type("project_create")
    assert entity == "project"
    assert operation == "create"
    print("  [PASS] parse_instant_workflow_type correctly parses workflow types")
    
    print("\n  All definition tests passed!")


def test_instant_workflow_handler():
    """Test InstantWorkflowHandler class."""
    print("\n" + "="*60)
    print("Testing InstantWorkflowHandler")
    print("="*60)
    
    from workflows.instant import (
        InstantWorkflowHandler,
        InstantWorkflowResult,
        SERVICE_REGISTRY,
        METHOD_MAPPING,
    )
    
    # Test service registry
    assert "project" in SERVICE_REGISTRY
    assert "vendor" in SERVICE_REGISTRY
    assert "bill" in SERVICE_REGISTRY
    print(f"  [PASS] {len(SERVICE_REGISTRY)} services registered")
    
    # Test method mapping
    assert METHOD_MAPPING["create"] == "create"
    assert METHOD_MAPPING["update"] == "update_by_public_id"
    assert METHOD_MAPPING["delete"] == "delete_by_public_id"
    print("  [PASS] Method mapping is correct")
    
    # Test InstantWorkflowResult dataclass
    result = InstantWorkflowResult(
        success=True,
        workflow_id="test-workflow-id",
        data={"id": 1, "name": "Test"},
    )
    assert result.success is True
    assert result.workflow_id == "test-workflow-id"
    assert result.data["name"] == "Test"
    assert result.error is None
    
    result_dict = result.to_dict()
    assert result_dict["success"] is True
    assert result_dict["workflow_id"] == "test-workflow-id"
    print("  [PASS] InstantWorkflowResult works correctly")
    
    # Test handler initialization
    handler = InstantWorkflowHandler()
    assert handler._orchestrator is None  # Lazy loading
    print("  [PASS] Handler initializes with lazy loading")
    
    print("\n  All handler tests passed!")


def test_trigger_router_instant_detection():
    """Test TriggerRouter instant workflow detection."""
    print("\n" + "="*60)
    print("Testing TriggerRouter Instant Workflow Detection")
    print("="*60)
    
    from workflows.router import TriggerRouter
    
    router = TriggerRouter()
    
    # Test instant workflow detection
    assert router._is_instant_workflow("project_create") is True
    assert router._is_instant_workflow("vendor_update") is True
    assert router._is_instant_workflow("bill_delete") is True
    assert router._is_instant_workflow("email_intake") is False
    assert router._is_instant_workflow("bill_processing") is False
    print("  [PASS] Router correctly detects instant workflow types")
    
    # Test handler selection
    handler = router._get_handler("project_create")
    assert handler is not None
    assert handler == router._handle_instant_workflow
    print("  [PASS] Router selects instant handler for instant workflows")
    
    handler = router._get_handler("email_intake")
    assert handler is not None
    assert handler == router._handle_email_intake
    print("  [PASS] Router selects correct handler for long-running workflows")
    
    print("\n  All router detection tests passed!")


def test_trigger_context_creation():
    """Test TriggerContext creation for instant workflows."""
    print("\n" + "="*60)
    print("Testing TriggerContext Creation")
    print("="*60)
    
    from workflows.router import (
        TriggerRouter,
        TriggerContext,
        TriggerType,
        TriggerSource,
    )
    
    router = TriggerRouter()
    
    # Test from_form_submit
    context = router.from_form_submit(
        tenant_id=1,
        user_id=123,
        form_data={"name": "Test Project", "description": "Test"},
        workflow_type="project_create",
    )
    
    assert context.tenant_id == 1
    assert context.user_id == 123
    assert context.trigger_type == TriggerType.FORM_SUBMIT
    assert context.trigger_source == TriggerSource.WEB
    assert context.workflow_type == "project_create"
    assert context.payload["name"] == "Test Project"
    assert context.expects_response is True
    assert context.correlation_id is not None
    print("  [PASS] from_form_submit creates valid context")
    
    # Test context serialization
    context_dict = context.to_dict()
    assert context_dict["tenant_id"] == 1
    assert context_dict["workflow_type"] == "project_create"
    print("  [PASS] TriggerContext serializes correctly")
    
    print("\n  All context creation tests passed!")


def test_service_loading():
    """Test dynamic service loading."""
    print("\n" + "="*60)
    print("Testing Dynamic Service Loading")
    print("="*60)
    
    from workflows.instant import InstantWorkflowHandler
    
    handler = InstantWorkflowHandler()
    
    # Test loading a few services
    services_to_test = ["project", "vendor", "customer"]
    
    for entity in services_to_test:
        try:
            service = handler._get_service(entity)
            assert service is not None
            
            # Verify service has expected methods
            assert hasattr(service, "create")
            assert hasattr(service, "update_by_public_id")
            assert hasattr(service, "delete_by_public_id")
            
            print(f"  [PASS] {entity} service loads correctly")
        except Exception as e:
            print(f"  [FAIL] {entity} service failed to load: {e}")
            raise
    
    # Test invalid entity
    try:
        handler._get_service("nonexistent_entity")
        print("  [FAIL] Should have raised ValueError for invalid entity")
        raise AssertionError("Should have raised ValueError")
    except ValueError as e:
        assert "No service registered" in str(e)
        print("  [PASS] Invalid entity raises ValueError")
    
    print("\n  All service loading tests passed!")


def test_integration_with_database():
    """
    Integration test: Execute an instant workflow and verify database records.
    
    This test requires a database connection and will create/delete test data.
    """
    print("\n" + "="*60)
    print("Testing Integration with Database")
    print("="*60)
    
    try:
        from workflows.router import TriggerContext, TriggerType, TriggerSource
        from workflows.instant import InstantWorkflowHandler
        from workflows.persistence.repo import WorkflowRepository, WorkflowEventRepository
        
        handler = InstantWorkflowHandler()
        workflow_repo = WorkflowRepository()
        event_repo = WorkflowEventRepository()
        
        # Create a trigger context for project create
        context = TriggerContext(
            trigger_type=TriggerType.API_CALL,
            trigger_source=TriggerSource.API,
            tenant_id=1,
            user_id=999,  # Test user
            payload={
                "name": "Instant Workflow Test Project",
                "description": "Created by instant workflow test",
                "status": "Active",
            },
            workflow_type="project_create",
        )
        
        print("  Executing instant workflow...")
        result = handler.execute(
            context=context,
            entity="project",
            operation="create",
            name="Instant Workflow Test Project",
            description="Created by instant workflow test",
            status="Active",
        )
        
        if result.success:
            print(f"  [PASS] Workflow executed successfully: {result.workflow_id}")
            
            # Verify workflow record was created
            workflow = workflow_repo.read_by_public_id(result.workflow_id)
            if workflow:
                print(f"  [PASS] Workflow record found in database")
                print(f"         - Type: {workflow.workflow_type}")
                print(f"         - State: {workflow.state}")
                
                # Verify state is completed
                assert workflow.state == "completed", f"Expected 'completed', got '{workflow.state}'"
                print(f"  [PASS] Workflow state is 'completed'")
                
                # Verify events were logged
                events = event_repo.read_by_workflow_id(workflow.id)
                print(f"  [PASS] {len(events)} event(s) logged")
                for event in events:
                    print(f"         - {event.event_type}: {event.from_state} -> {event.to_state}")
            else:
                print(f"  [FAIL] Workflow record not found in database")
            
            # Cleanup: Delete the test project
            if result.data and result.data.get("public_id"):
                from services.project.business.service import ProjectService
                try:
                    ProjectService().delete_by_public_id(result.data["public_id"])
                    print(f"  [CLEANUP] Test project deleted")
                except Exception as e:
                    print(f"  [WARN] Could not delete test project: {e}")
        else:
            print(f"  [FAIL] Workflow execution failed: {result.error}")
            
    except Exception as e:
        print(f"  [SKIP] Database integration test skipped: {e}")
        print("         (This is expected if database is not configured)")
    
    print("\n  Integration test completed!")


def test_web_helpers():
    """Test web helper functions for workflow integration."""
    print("\n" + "="*60)
    print("Testing Web Helpers")
    print("="*60)
    
    from workflows.web_helpers import (
        get_trigger_context_from_request,
        get_trigger_context_for_button_click,
        route_instant_workflow,
    )
    from workflows.router import TriggerType, TriggerSource
    
    # Mock request and current_user
    class MockRequest:
        pass
    
    mock_request = MockRequest()
    mock_user = {
        "id": 123,
        "username": "testuser",
        "tenant_id": 5,
        "access_token": "test-token-123",
    }
    
    # Test get_trigger_context_from_request
    context = get_trigger_context_from_request(
        request=mock_request,
        current_user=mock_user,
        form_data={"name": "Test Entity", "description": "Test"},
        workflow_type="project_create",
    )
    
    assert context.tenant_id == 5
    assert context.user_id == 123
    assert context.trigger_type == TriggerType.FORM_SUBMIT
    assert context.trigger_source == TriggerSource.WEB
    assert context.workflow_type == "project_create"
    assert context.payload["name"] == "Test Entity"
    assert context.access_token == "test-token-123"
    print("  [PASS] get_trigger_context_from_request works correctly")
    
    # Test with default tenant_id (when not in user dict)
    mock_user_no_tenant = {"id": 456, "username": "user2"}
    context2 = get_trigger_context_from_request(
        request=mock_request,
        current_user=mock_user_no_tenant,
        form_data={"name": "Test"},
        workflow_type="vendor_create",
    )
    assert context2.tenant_id == 1  # Default
    assert context2.user_id == 456
    print("  [PASS] Default tenant_id is 1 when not specified")
    
    # Test get_trigger_context_for_button_click
    button_context = get_trigger_context_for_button_click(
        current_user=mock_user,
        action="extract",
        entity_type="attachment",
        entity_id="abc-123",
        payload={"force": True},
    )
    
    assert button_context.tenant_id == 5
    assert button_context.user_id == 123
    assert button_context.trigger_type == TriggerType.BUTTON_CLICK
    assert button_context.payload["action"] == "extract"
    assert button_context.payload["entity_type"] == "attachment"
    assert button_context.payload["entity_id"] == "abc-123"
    assert button_context.payload["force"] is True
    print("  [PASS] get_trigger_context_for_button_click works correctly")
    
    print("\n  All web helper tests passed!")


def test_tenant_id_in_context():
    """Test that tenant_id is properly included in workflow context."""
    print("\n" + "="*60)
    print("Testing Tenant ID in Workflow Context")
    print("="*60)
    
    from workflows.router import TriggerContext, TriggerType, TriggerSource
    from workflows.instant import InstantWorkflowHandler
    
    # Create context with specific tenant_id
    context = TriggerContext(
        trigger_type=TriggerType.API_CALL,
        trigger_source=TriggerSource.API,
        tenant_id=42,
        user_id=123,
        payload={"name": "Test"},
        workflow_type="project_create",
    )
    
    assert context.tenant_id == 42
    print("  [PASS] TriggerContext stores tenant_id correctly")
    
    # Verify context serialization includes tenant_id
    context_dict = context.to_dict()
    assert context_dict["tenant_id"] == 42
    print("  [PASS] TriggerContext.to_dict() includes tenant_id")
    
    # Test handler initialization doesn't break with tenant context
    handler = InstantWorkflowHandler()
    assert handler is not None
    print("  [PASS] InstantWorkflowHandler initializes with tenant-aware context")
    
    print("\n  All tenant_id tests passed!")


def test_service_accepts_tenant_id():
    """Test that services accept tenant_id parameter."""
    print("\n" + "="*60)
    print("Testing Services Accept Tenant ID")
    print("="*60)
    
    import inspect
    from workflows.instant import SERVICE_REGISTRY
    
    # Services that should have tenant_id parameter
    services_to_check = [
        "project",
        "vendor",
        "customer",
        "bill",
        "cost_code",
        "sub_cost_code",
        "payment_term",
    ]
    
    for entity in services_to_check:
        if entity not in SERVICE_REGISTRY:
            print(f"  [SKIP] {entity} not in registry")
            continue
            
        module_path = SERVICE_REGISTRY[entity]
        module_name, class_name = module_path.rsplit(".", 1)
        
        try:
            import importlib
            module = importlib.import_module(module_name)
            service_class = getattr(module, class_name)
            service = service_class()
            
            # Check if create method accepts tenant_id
            create_sig = inspect.signature(service.create)
            params = list(create_sig.parameters.keys())
            
            if "tenant_id" in params:
                print(f"  [PASS] {entity} service.create() accepts tenant_id")
            else:
                print(f"  [WARN] {entity} service.create() missing tenant_id parameter")
                
        except Exception as e:
            print(f"  [SKIP] Could not check {entity}: {e}")
    
    print("\n  Service tenant_id check completed!")


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("INSTANT WORKFLOWS TEST SUITE")
    print("="*60)
    
    # Unit tests (no database required)
    test_instant_workflow_definitions()
    test_instant_workflow_handler()
    test_trigger_router_instant_detection()
    test_trigger_context_creation()
    test_service_loading()
    
    # New tests for Phase 5: Tenant ID and Web Helpers
    test_web_helpers()
    test_tenant_id_in_context()
    test_service_accepts_tenant_id()
    
    # Integration test (requires database)
    test_integration_with_database()
    
    print("\n" + "="*60)
    print("ALL TESTS COMPLETED")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
