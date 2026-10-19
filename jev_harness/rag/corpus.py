"""In-memory help-center corpus (verified-cascade cookbook excerpts + more)."""

from __future__ import annotations

from typing import Dict, List

# (doc_id, title, text)
CORPUS: List[Dict[str, str]] = [
    {
        "id": "workspace-plan",
        "title": "Workspace plans and seats",
        "text": (
            "Workspace plans: Starter includes 5 seats and 10GB storage; "
            "Pro includes 25 seats, 100GB storage and SSO; Enterprise is "
            "unlimited seats with audit logs and a 99.9% uptime SLA. Seat "
            "changes are prorated to the day. Downgrading takes effect at "
            "the next billing cycle."
        ),
    },
    {
        "id": "billing-refunds",
        "title": "Billing, invoices and refunds",
        "text": (
            "Refunds are issued to the original payment method within 5-10 "
            "business days. Annual plans cancelled within 30 days receive a "
            "full refund; after 30 days, refunds are pro-rated. Failed "
            "payments retry automatically for 7 days before the workspace "
            "is paused. Invoices are available under Settings > Billing."
        ),
    },
    {
        "id": "data-export",
        "title": "Exporting your data",
        "text": (
            "Data export: workspace admins can request a full export "
            "(JSON and CSV) from Settings > Security. Exports are prepared "
            "within 24 hours and delivered via a signed download link that "
            "expires after 72 hours. API-based exports are limited to "
            "10,000 rows per request."
        ),
    },
    {
        "id": "webhook-troubleshooting",
        "title": "Webhook delivery failures",
        "text": (
            "If webhook requests are failing with an error, first check the "
            "endpoint URL is reachable and returns 2xx. Verify the "
            "signing secret matches — mismatches return 401. Timeouts "
            "occur when the endpoint takes longer than 10 seconds; "
            "delivery is retried 5 times with exponential backoff before "
            "the subscription is paused. Use the delivery log to inspect "
            "request and response bodies."
        ),
    },
    {
        "id": "sso-setup",
        "title": "SSO and SAML setup",
        "text": (
            "SSO via SAML 2.0 is available on Pro and Enterprise plans. "
            "Configure the IdP metadata URL under Settings > Security > "
            "SSO. Just-in-time provisioning creates accounts on first "
            "login. SP-initiated and IdP-initiated flows are both "
            "supported."
        ),
    },
    {
        "id": "api-rate-limits",
        "title": "API rate limits",
        "text": (
            "API rate limits: 600 requests per minute per workspace on "
            "Starter, 3000 on Pro, and 12000 on Enterprise. Rate limit "
            "responses include X-RateLimit-Remaining and Retry-After "
            "headers. Burst traffic should be smoothed with an "
            "exponential backoff."
        ),
    },
    {
        "id": "password-reset",
        "title": "Resetting your password",
        "text": (
            "To reset your password, use the 'Forgot password' link on the "
            "sign-in page. A reset email is sent within 2 minutes and is "
            "valid for 60 minutes. If you use SSO, password resets are "
            "handled by your identity provider."
        ),
    },
    {
        "id": "integrations-slack",
        "title": "Slack integration",
        "text": (
            "The Slack integration posts alerts and digests to a channel "
            "of your choice. Connect it under Settings > Integrations. "
            "Notifications are batched every 15 minutes to avoid channel "
            "spam. Unfurling deep-links to records requires the "
            "workspace slug."
        ),
    },
    {
        "id": "uptime-sla",
        "title": "Uptime and SLA",
        "text": (
            "Our status page reports real-time uptime. Starter plans have "
            "no SLA; Pro has a 99.9% SLA with service credits of 10% for "
            "each hour of downtime beyond the monthly allowance; "
            "Enterprise has a 99.99% SLA with a dedicated support channel."
        ),
    },
]
