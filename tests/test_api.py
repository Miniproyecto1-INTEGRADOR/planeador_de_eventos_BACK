from datetime import date, timedelta

from fastapi.testclient import TestClient

import app.main as api_module
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


def test_today_filters_and_orders_subtasks_without_database(monkeypatch):
    today = date.today()
    event_id = '550e8400-e29b-41d4-a716-446655440000'
    captured = {}
    rows = [
        {'id': 'late-overdue', 'title': 'Vencida reciente', 'target_date': (today - timedelta(days=1)).isoformat(), 'estimated_minutes': 15},
        {'id': 'far-overdue', 'title': 'Vencida antigua', 'target_date': (today - timedelta(days=4)).isoformat(), 'estimated_minutes': 60},
        {'id': 'today-long', 'title': 'Tarea larga', 'target_date': today.isoformat(), 'estimated_minutes': 90},
        {'id': 'today-short', 'title': 'Tarea corta', 'target_date': today.isoformat(), 'estimated_minutes': 30},
        {'id': 'upcoming', 'title': 'Próxima', 'target_date': (today + timedelta(days=1)).isoformat(), 'estimated_minutes': 20},
        {'id': 'undated', 'title': 'Sin fecha', 'target_date': None, 'estimated_minutes': 10},
    ]

    def fake_get_rows(table, filters=None, select='*'):
        captured['table'] = table
        captured['filters'] = filters
        return rows

    monkeypatch.setattr(api_module, '_get_rows', fake_get_rows)

    response = client.get('/api/hoy/', params={'event_id': event_id, 'status': 'pending'})

    assert response.status_code == 200
    assert captured == {
        'table': 'subtasks',
        'filters': {'status': 'eq.pending', 'event_id': f'eq.{event_id}'},
    }
    payload = response.json()
    assert [item['id'] for item in payload['vencidas']] == ['far-overdue', 'late-overdue']
    assert [item['id'] for item in payload['hoy']] == ['today-short', 'today-long']
    assert [item['id'] for item in payload['proximas']] == ['upcoming', 'undated']


def test_today_defaults_to_excluding_completed_and_rejects_unknown_status(monkeypatch):
    captured = {}

    def fake_get_rows(table, filters=None, select='*'):
        captured['filters'] = filters
        return []

    monkeypatch.setattr(api_module, '_get_rows', fake_get_rows)

    response = client.get('/api/hoy/')
    assert response.status_code == 200
    assert captured['filters'] == {'status': 'neq.done'}

    invalid_response = client.get('/api/hoy/', params={'status': 'unknown'})
    assert invalid_response.status_code == 400
