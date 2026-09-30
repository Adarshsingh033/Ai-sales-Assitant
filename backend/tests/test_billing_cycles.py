import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_billing_cycle(client: AsyncClient, super_admin_token: str):
    headers = {"Authorization": f"Bearer {super_admin_token}"}
    payload = {
        "name": "Bi-Annual",
        "duration_months": 6,
        "description": "Billed every 6 months",
        "is_active": True,
    }
    response = await client.post(
        "/api/v1/super-admin/billing-cycles",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Bi-Annual"
    assert data["duration_months"] == 6
    assert data["slug"] == "bi-annual"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_list_billing_cycles(client: AsyncClient, super_admin_token: str):
    headers = {"Authorization": f"Bearer {super_admin_token}"}
    response = await client.get(
        "/api/v1/super-admin/billing-cycles",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_update_billing_cycle(client: AsyncClient, super_admin_token: str):
    headers = {"Authorization": f"Bearer {super_admin_token}"}
    # Create cycle first
    create_res = await client.post(
        "/api/v1/super-admin/billing-cycles",
        json={"name": "Tri-Monthly", "duration_months": 3, "description": "Every 3 months"},
        headers=headers,
    )
    cycle_id = create_res.json()["id"]

    # Update
    update_res = await client.patch(
        f"/api/v1/super-admin/billing-cycles/{cycle_id}",
        json={"name": "Quarterly Plan", "description": "Updated description"},
        headers=headers,
    )
    assert update_res.status_code == 200
    assert update_res.json()["name"] == "Quarterly Plan"
    assert update_res.json()["description"] == "Updated description"


@pytest.mark.asyncio
async def test_update_billing_cycle_status(client: AsyncClient, super_admin_token: str):
    headers = {"Authorization": f"Bearer {super_admin_token}"}
    create_res = await client.post(
        "/api/v1/super-admin/billing-cycles",
        json={"name": "Test Status Cycle", "duration_months": 1},
        headers=headers,
    )
    cycle_id = create_res.json()["id"]

    status_res = await client.patch(
        f"/api/v1/super-admin/billing-cycles/{cycle_id}/status",
        json={"is_active": False},
        headers=headers,
    )
    assert status_res.status_code == 200
    assert status_res.json()["is_active"] is False


@pytest.mark.asyncio
async def test_delete_billing_cycle(client: AsyncClient, super_admin_token: str):
    headers = {"Authorization": f"Bearer {super_admin_token}"}
    create_res = await client.post(
        "/api/v1/super-admin/billing-cycles",
        json={"name": "To Be Deleted", "duration_months": 2},
        headers=headers,
    )
    cycle_id = create_res.json()["id"]

    del_res = await client.delete(
        f"/api/v1/super-admin/billing-cycles/{cycle_id}",
        headers=headers,
    )
    assert del_res.status_code == 204

    get_res = await client.get(
        f"/api/v1/super-admin/billing-cycles/{cycle_id}",
        headers=headers,
    )
    assert get_res.status_code == 404
