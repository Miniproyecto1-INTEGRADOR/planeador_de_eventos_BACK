from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get('/api/health/')
    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] in {'ok', 'degraded'}


def test_create_event_and_subtasks():
    event_response = client.post(
        '/api/eventos/',
        json={
            'name': 'Boda María y Juan',
            'event_type': 'Boda',
            'event_date': '2026-10-18T18:00:00',
            'color': '#FF6B6B',
            'user_id': '11111111-1111-1111-1111-111111111111'
        }
    )

    assert event_response.status_code == 201, event_response.text
    event = event_response.json()
    assert event['name'] == 'Boda María y Juan'
    assert 'id' in event

    subtask_response = client.post(
        f"/api/eventos/{event['id']}/subtareas/",
        json={
            'title': 'Reservar salón',
            'description': 'Confirmar disponibilidad y pago del alquiler.',
            'target_date': '2026-09-25',
            'estimated_minutes': 90,
            'status': 'pending'
        }
    )

    assert subtask_response.status_code == 201, subtask_response.text
    subtask = subtask_response.json()
    assert subtask['title'] == 'Reservar salón'
    assert subtask['event_id'] == event['id']
    assert subtask['estimated_minutes'] == 90


def test_create_subtask_rejects_invalid_duration():
    event_response = client.post(
        '/api/eventos/',
        json={
            'name': 'Cumpleaños',
            'event_type': 'Cumpleaños',
            'event_date': '2026-11-05T19:30:00',
            'user_id': '22222222-2222-2222-2222-222222222222'
        }
    )
    event = event_response.json()

    response = client.post(
        f"/api/eventos/{event['id']}/subtareas/",
        json={
            'title': 'Enviar invitaciones',
            'target_date': '2026-09-27',
            'estimated_minutes': 0,
            'status': 'pending'
        }
    )

    assert response.status_code == 400


def test_event_cycle_and_today_grouping():
    event_response = client.post(
        '/api/eventos/',
        json={
            'name': 'Evento prueba sprint 1',
            'event_type': 'Corporativo',
            'event_date': '2026-12-10T10:00:00',
            'user_id': '33333333-3333-3333-3333-333333333333'
        }
    )
    event = event_response.json()

    subtask_response = client.post(
        f"/api/eventos/{event['id']}/subtareas/",
        json={
            'title': 'Preparar agenda',
            'description': 'Definir agenda y logística.',
            'target_date': '2026-09-22',
            'estimated_minutes': 120,
            'status': 'pending'
        }
    )
    subtask = subtask_response.json()

    patch_response = client.patch(
        f"/api/eventos/{event['id']}/subtareas/{subtask['id']}",
        json={'status': 'done', 'description': 'Lista final'}
    )
    assert patch_response.status_code == 200, patch_response.text

    today_response = client.get('/api/hoy/')
    assert today_response.status_code == 200
    payload = today_response.json()
    assert 'vencidas' in payload
    assert 'hoy' in payload
    assert 'proximas' in payload


def test_delete_event_removes_subtasks():
    event_response = client.post(
        '/api/eventos/',
        json={
            'name': 'Evento a eliminar',
            'event_type': 'Boda',
            'event_date': '2026-10-15T15:00:00',
            'user_id': '44444444-4444-4444-4444-444444444444'
        }
    )
    event = event_response.json()

    subtask_response = client.post(
        f"/api/eventos/{event['id']}/subtareas/",
        json={
            'title': 'Confirmar catering',
            'target_date': '2026-09-24',
            'estimated_minutes': 80,
            'status': 'pending'
        }
    )
    subtask = subtask_response.json()

    delete_response = client.delete(f"/api/eventos/{event['id']}")
    assert delete_response.status_code == 200, delete_response.text

    fetch_event = client.get(f"/api/eventos/{event['id']}")
    assert fetch_event.status_code == 404

    list_subtasks = client.get(f"/api/eventos/{event['id']}/subtareas/")
    assert list_subtasks.status_code == 404
