"""Provider APIs must remain isolated and explicitly routed."""

from app.main import app


def provider_routes(provider: str) -> set[tuple[str, str]]:
    prefix = f"/api/v1/integrations/{provider}"
    return {
        (method, route.path)
        for route in app.routes
        if route.path == prefix or route.path.startswith(prefix + "/")
        for method in route.methods
    }


def test_provider_management_routes_are_not_mixed_into_platform_admin():
    old_prefixes = (
        "/api/v1/platform-admin/integrations/propertyradar",
        "/api/v1/platform-admin/integrations/batchdata",
    )
    assert not any(
        route.path == prefix or route.path.startswith(prefix + "/")
        for route in app.routes
        for prefix in old_prefixes
    )
    retired_aggregate_data_routes = {
        "/api/v1/integrations/propertyradar/imports/preview",
        "/api/v1/integrations/propertyradar/imports/run",
        "/api/v1/integrations/batchdata/runs/plan",
        "/api/v1/integrations/batchdata/runs/validate",
        "/api/v1/integrations/batchdata/runs/approve",
        "/api/v1/integrations/batchdata/runs/execute",
        "/api/v1/integrations/dealmachine/sync/initial",
        "/api/v1/integrations/dealmachine/sync/all",
        "/api/v1/integrations/dealmachine/enrichment/run",
    }
    assert not retired_aggregate_data_routes.intersection(
        {route.path for route in app.routes}
    )


def test_propertyradar_has_a_dedicated_backend_api_group():
    routes = provider_routes("propertyradar")
    assert {
        ("GET", "/api/v1/integrations/propertyradar"),
        ("PUT", "/api/v1/integrations/propertyradar/config"),
        ("POST", "/api/v1/integrations/propertyradar/connection/test"),
        ("POST", "/api/v1/integrations/propertyradar/properties/search"),
        ("POST", "/api/v1/integrations/propertyradar/properties/details"),
        ("POST", "/api/v1/integrations/propertyradar/contacts/enrich"),
        ("POST", "/api/v1/integrations/propertyradar/lists/prepare"),
        ("POST", "/api/v1/integrations/propertyradar/monitoring/validate"),
        ("POST", "/api/v1/integrations/propertyradar/monitoring/enable"),
        ("POST", "/api/v1/integrations/propertyradar/monitoring/pause"),
        ("POST", "/api/v1/integrations/propertyradar/webhook/register"),
        ("POST", "/api/v1/integrations/propertyradar/events/retry"),
        ("GET", "/api/v1/integrations/propertyradar/lists"),
        ("GET", "/api/v1/integrations/propertyradar/events"),
        ("GET", "/api/v1/integrations/propertyradar/usage"),
        ("GET", "/api/v1/integrations/propertyradar/api-logs"),
        ("GET", "/api/v1/integrations/propertyradar/skiptrace-jobs"),
        ("GET", "/api/v1/integrations/propertyradar/audit"),
    } <= routes
    assert all(
        "{action}" not in path and "{resource}" not in path for _, path in routes
    )


def test_batchdata_has_a_dedicated_backend_api_group():
    routes = provider_routes("batchdata")
    assert {
        ("GET", "/api/v1/integrations/batchdata"),
        ("PUT", "/api/v1/integrations/batchdata/config"),
        ("POST", "/api/v1/integrations/batchdata/connection/test"),
        ("POST", "/api/v1/integrations/batchdata/monitoring/validate"),
        ("POST", "/api/v1/integrations/batchdata/products/quick-lists/search"),
        ("POST", "/api/v1/integrations/batchdata/products/basic-property/search"),
        ("POST", "/api/v1/integrations/batchdata/products/listing-data/search"),
        ("POST", "/api/v1/integrations/batchdata/products/pre-foreclosure/search"),
        ("POST", "/api/v1/integrations/batchdata/products/contact-enrichment"),
        ("GET", "/api/v1/integrations/batchdata/runs"),
        ("GET", "/api/v1/integrations/batchdata/properties"),
        ("GET", "/api/v1/integrations/batchdata/memberships"),
        ("GET", "/api/v1/integrations/batchdata/api-calls"),
        ("GET", "/api/v1/integrations/batchdata/webhooks"),
        ("GET", "/api/v1/integrations/batchdata/saved-files"),
        ("GET", "/api/v1/integrations/batchdata/audit"),
    } <= routes
    assert all(
        "{action}" not in path and "{resource}" not in path for _, path in routes
    )


def test_dealmachine_has_separate_property_and_contact_calls():
    routes = provider_routes("dealmachine")
    assert {
        ("POST", "/api/v1/integrations/dealmachine/properties/search"),
        ("POST", "/api/v1/integrations/dealmachine/properties/details"),
        ("POST", "/api/v1/integrations/dealmachine/contacts/enrich"),
    } <= routes
    assert not any(
        path.startswith("/api/v1/integrations/dealmachine/sync/") for _, path in routes
    )
