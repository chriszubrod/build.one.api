from core.workflow.business.instant import _build_service_kwargs


def no_tenant(a, b=None):
    pass


def with_tenant(a, tenant_id=None):
    pass


def with_var_kwargs(a, **kwargs):
    pass


def test_no_tenant_param_omits_tenant_id():
    kwargs = {"a": 1, "b": 2}
    built = _build_service_kwargs(no_tenant, "t1", kwargs)
    assert "tenant_id" not in built
    no_tenant(**built)  # must not raise TypeError


def test_explicit_tenant_id_param_injects():
    built = _build_service_kwargs(with_tenant, "t1", {"a": 1})
    assert built["tenant_id"] == "t1"


def test_var_keyword_param_injects():
    built = _build_service_kwargs(with_var_kwargs, "t1", {"a": 1})
    assert built["tenant_id"] == "t1"


def test_does_not_mutate_caller_kwargs():
    kwargs = {"a": 1}
    _build_service_kwargs(no_tenant, "t1", kwargs)
    assert "tenant_id" not in kwargs
