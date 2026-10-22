"""Stripe draft invoice creation for billable Toggl project ranges."""

from datetime import datetime

import requests

from hours_csv_export import get_project_entries_for_range
from integrations import load_integration_settings

STRIPE_API_BASE = "https://api.stripe.com/v1"


def create_draft_invoice_for_project_range(
    *,
    project_id,
    project_name,
    customer_id,
    hourly_rate,
    start_d,
    end_d,
    due_in_days=30,
):
    """Create a Stripe draft invoice and attached invoice item for one project range."""
    stripe_key = load_integration_settings().get("STRIPE_API_KEY")
    if not stripe_key:
        raise RuntimeError("STRIPE_API_KEY is not configured. Add it in Settings > Integrations.")
    if not customer_id:
        raise RuntimeError(f"No Stripe customer configured for {project_name}")

    entries = get_project_entries_for_range(project_id, project_name, start_d, end_d)
    total_hours = sum(max(0, entry.get("duration", 0)) for entry in entries) / 3600
    if total_hours <= 0:
        raise RuntimeError(f"No billable hours found for {project_name}")

    amount_cents = round(total_hours * float(hourly_rate) * 100)
    summary = (
        f"{project_name} work for {start_d.strftime('%b %-d, %Y')} to "
        f"{end_d.strftime('%b %-d, %Y')} ({total_hours:.2f}h @ USD {float(hourly_rate):.0f}/hr)"
    )
    footer = _build_hours_breakdown(entries)

    invoice_payload = {
        "customer": customer_id,
        "auto_advance": "false",
        "collection_method": "send_invoice",
        "days_until_due": str(int(due_in_days)),
        "pending_invoice_items_behavior": "exclude",
        "description": summary,
        "footer": footer,
        "metadata[toggl_project]": project_name,
        "metadata[toggl_project_id]": str(project_id),
        "metadata[date_range_start]": start_d.isoformat(),
        "metadata[date_range_end]": end_d.isoformat(),
        "metadata[hourly_rate_usd]": f"{float(hourly_rate):.2f}",
        "metadata[hours_total]": f"{total_hours:.2f}",
        "metadata[freelance_tracker_workflow]": "draft_invoice",
    }

    invoice = _stripe_post("/invoices", stripe_key, invoice_payload)
    invoice_id = invoice["id"]

    try:
        invoice_item = _stripe_post(
            "/invoiceitems",
            stripe_key,
            {
                "customer": customer_id,
                "invoice": invoice_id,
                "currency": "usd",
                "amount": str(amount_cents),
                "description": summary,
                "metadata[toggl_project]": project_name,
                "metadata[toggl_project_id]": str(project_id),
                "metadata[date_range_start]": start_d.isoformat(),
                "metadata[date_range_end]": end_d.isoformat(),
                "metadata[hourly_rate_usd]": f"{float(hourly_rate):.2f}",
                "metadata[hours_total]": f"{total_hours:.2f}",
                "metadata[freelance_tracker_workflow]": "draft_invoice",
            },
        )
    except Exception:
        _stripe_delete(f"/invoices/{invoice_id}", stripe_key)
        raise

    return {
        "invoice_id": invoice_id,
        "invoice_item_id": invoice_item["id"],
        "dashboard_url": f"https://dashboard.stripe.com/invoices/{invoice_id}",
        "summary": summary,
        "footer": footer,
        "total_hours": total_hours,
        "amount_cents": amount_cents,
        "amount_usd": amount_cents / 100,
        "date_range_label": (
            f"{start_d.strftime('%b %-d, %Y')} to {end_d.strftime('%b %-d, %Y')}"
        ),
    }


def list_customers(api_key=None):
    """Fetch Stripe customers for picker UIs."""
    stripe_key = api_key or load_integration_settings().get("STRIPE_API_KEY")
    if not stripe_key:
        return []

    customers = []
    starting_after = None
    while True:
        payload = {"limit": 100}
        if starting_after:
            payload["starting_after"] = starting_after
        response = requests.get(
            f"{STRIPE_API_BASE}/customers",
            auth=(stripe_key, ""),
            params=payload,
            timeout=30,
        )
        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise RuntimeError(_format_stripe_error(response)) from exc
        body = response.json()
        page = body.get("data", [])
        customers.extend(page)
        if not body.get("has_more") or not page:
            break
        starting_after = page[-1]["id"]

    customers.sort(key=lambda customer: _customer_display_name(customer).lower())
    return [
        {
            "id": customer["id"],
            "name": customer.get("name") or "",
            "email": customer.get("email") or "",
            "display_name": _customer_display_name(customer),
        }
        for customer in customers
    ]


def _build_hours_breakdown(entries):
    lines = []
    for entry in sorted(entries, key=lambda item: item.get("start") or ""):
        start = entry.get("start")
        if not start:
            continue
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone()
        hours = max(0, entry.get("duration", 0)) / 3600
        description = (entry.get("description") or "No description").strip()
        lines.append(
            f"- {start_dt.strftime('%b %-d')}: {hours:.2f}h {description}"
        )
    return "Hours breakdown:\n" + "\n".join(lines)


def _customer_display_name(customer):
    name = (customer.get("name") or "").strip()
    email = (customer.get("email") or "").strip()
    customer_id = customer.get("id", "")
    if name and email:
        return f"{name} <{email}>"
    if name:
        return name
    if email:
        return email
    return customer_id


def _stripe_post(path, api_key, data):
    response = requests.post(
        f"{STRIPE_API_BASE}{path}",
        auth=(api_key, ""),
        data=data,
        timeout=30,
    )
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        raise RuntimeError(_format_stripe_error(response)) from exc
    return response.json()


def _stripe_delete(path, api_key):
    response = requests.delete(
        f"{STRIPE_API_BASE}{path}",
        auth=(api_key, ""),
        timeout=30,
    )
    if response.status_code >= 400:
        return None
    return response.json()


def _format_stripe_error(response):
    try:
        body = response.json()
    except ValueError:
        return f"Stripe request failed ({response.status_code})"
    err = body.get("error") or {}
    message = err.get("message") or f"Stripe request failed ({response.status_code})"
    code = err.get("code")
    if code:
        return f"{message} [{code}]"
    return message
