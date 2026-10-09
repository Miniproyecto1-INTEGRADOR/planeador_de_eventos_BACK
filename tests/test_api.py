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


def test_daily_limit_defaults_to_six_hours(monkeypatch):
    monkeypatch.setattr(api_module, '_get_row', lambda *args, **kwargs: None)

    response = client.get('/api/usuarios/11111111-1111-1111-1111-111111111111/limite')

    assert response.status_code == 200
    assert response.json()['daily_limit_hours'] == 6


def test_daily_limit_update_persists_minutes_and_rejects_out_of_range(monkeypatch):
    updates = []

    def fake_update_row(table, filters, payload):
        updates.append((table, filters, payload))
        return payload

    monkeypatch.setattr(api_module, '_get_row', lambda *args, **kwargs: {'daily_limit_minutes': 360})
    monkeypatch.setattr(api_module, '_get_rows', lambda *args, **kwargs: [])
    monkeypatch.setattr(api_module, '_update_row', fake_update_row)

    response = client.put('/api/usuarios/11111111-1111-1111-1111-111111111111/limite', params={'value': 4})
    low_response = client.put('/api/usuarios/11111111-1111-1111-1111-111111111111/limite', params={'value': 0})
    high_response = client.put('/api/usuarios/11111111-1111-1111-1111-111111111111/limite', params={'value': 17})

    assert response.status_code == 200
    assert response.json()['daily_limit_hours'] == 4
    assert updates == [(
        'users',
        {'id': 'eq.11111111-1111-1111-1111-111111111111', 'select': 'id,daily_limit_minutes'},
        {'daily_limit_minutes': 240},
    )]
    assert low_response.status_code == 400
    assert high_response.status_code == 400


def test_daily_limit_update_reports_missing_profile(monkeypatch):
    monkeypatch.setattr(api_module, '_get_row', lambda *args, **kwargs: None)
    monkeypatch.setattr(api_module, '_get_rows', lambda *args, **kwargs: [])
    monkeypatch.setattr(api_module, '_update_row', lambda *args, **kwargs: None)

    response = client.put('/api/usuarios/11111111-1111-1111-1111-111111111111/limite', params={'value': 4})

    assert response.status_code == 404


def test_daily_limit_cannot_be_reduced_below_todays_planned_load(monkeypatch):
    today = api_module.date.today().isoformat()
    updates = []
    subtasks = [
        {
            'id': 'subtask-1',
            'event_id': 'event-1',
            'title': 'Preparar decoración',
            'target_date': today,
            'estimated_minutes': 180,
        },
        {
            'id': 'subtask-2',
            'event_id': 'event-2',
            'title': 'Confirmar proveedor',
            'target_date': today,
            'estimated_minutes': 120,
        },
    ]

    monkeypatch.setattr(api_module, '_get_row', lambda *args, **kwargs: {'daily_limit_minutes': 360})
    monkeypatch.setattr(api_module, '_get_rows', lambda *args, **kwargs: subtasks)
    monkeypatch.setattr(
        api_module,
        '_update_row',
        lambda *args, **kwargs: updates.append(args) or kwargs.get('payload'),
    )

    response = client.put(
        '/api/usuarios/11111111-1111-1111-1111-111111111111/limite',
        params={'value': 4},
    )

    assert response.status_code == 409
    assert response.json()['detail'] == {
        'code': 'daily_capacity_exceeded',
        'conflict_type': 'daily_limit_reduction',
        'target_date': today,
        'planned_minutes': 300,
        'limit_minutes': 240,
        'current_limit_minutes': 360,
        'subtasks': subtasks,
    }
    assert updates == []


def test_daily_limit_stays_blocked_when_current_limit_is_already_too_low(monkeypatch):
    today = api_module.date.today().isoformat()
    updates = []
    subtasks = [{
        'id': 'subtask-1',
        'event_id': 'event-1',
        'title': 'Preparar decoración',
        'target_date': today,
        'estimated_minutes': 240,
    }]

    monkeypatch.setattr(api_module, '_get_row', lambda *args, **kwargs: {'daily_limit_minutes': 120})
    monkeypatch.setattr(api_module, '_get_rows', lambda *args, **kwargs: subtasks)
    monkeypatch.setattr(api_module, '_update_row', lambda *args, **kwargs: updates.append(args))

    response = client.put(
        '/api/usuarios/11111111-1111-1111-1111-111111111111/limite',
        params={'value': 3},
    )

    assert response.status_code == 409
    assert response.json()['detail']['planned_minutes'] == 240
    assert response.json()['detail']['limit_minutes'] == 180
    assert updates == []


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


def test_create_subtask_rejects_missing_or_later_target_date(monkeypatch):
    event_id = '550e8400-e29b-41d4-a716-446655440000'
    inserted_rows = []
    monkeypatch.setattr(
        api_module,
        '_get_row',
        lambda table, filters, select='*': {'id': event_id, 'event_date': '2026-10-18T18:00:00'},
    )
    monkeypatch.setattr(api_module, '_insert_row', lambda table, payload: inserted_rows.append(payload))

    base_payload = {
        'title': 'Confirmar catering',
        'estimated_minutes': 60,
        'status': 'pending',
    }
    future_response = client.post(
        f'/api/eventos/{event_id}/subtareas/',
        json={**base_payload, 'target_date': '2026-10-19'},
    )
    missing_response = client.post(f'/api/eventos/{event_id}/subtareas/', json=base_payload)

    assert future_response.status_code == 400
    assert 'posterior a la fecha del evento' in future_response.json()['detail']
    assert missing_response.status_code == 400
    assert inserted_rows == []


def test_create_subtask_rejects_daily_capacity_without_inserting(monkeypatch):
    event_id = '550e8400-e29b-41d4-a716-446655440000'
    inserted_rows = []

    def fake_get_row(table, filters, select='*'):
        if table == 'events':
            return {'id': event_id, 'event_date': '2026-11-05T18:00:00', 'user_id': '11111111-1111-1111-1111-111111111111'}
        if table == 'users':
            return {'daily_limit_minutes': 360}
        return None

    monkeypatch.setattr(api_module, '_get_row', fake_get_row)
    monkeypatch.setattr(
        api_module,
        '_get_rows',
        lambda *args, **kwargs: [{'id': 'existing-task', 'estimated_minutes': 300}],
    )
    monkeypatch.setattr(api_module, '_insert_row', lambda table, payload: inserted_rows.append(payload))

    response = client.post(
        f'/api/eventos/{event_id}/subtareas/',
        json={
            'title': 'Añadir gestión de siete horas',
            'target_date': '2026-10-20',
            'estimated_minutes': 420,
            'status': 'pending',
        },
    )

    assert response.status_code == 409
    assert response.json()['detail']['planned_minutes'] == 720
    assert response.json()['detail']['limit_minutes'] == 360
    assert inserted_rows == []


def test_initial_plan_rolls_back_when_subtask_is_after_event(monkeypatch):
    inserted_events = []
    deleted_rows = []

    def fake_insert_row(table, payload):
        if table == 'events':
            inserted_events.append(payload)
        return payload

    monkeypatch.setattr(api_module, '_insert_row', fake_insert_row)
    monkeypatch.setattr(api_module, '_delete_rows', lambda table, filters: deleted_rows.append(table) or [])

    response = client.post(
        '/api/eventos/plan-inicial/',
        json={
            'name': 'Evento de prueba',
            'event_type': 'Boda',
            'event_date': '2026-10-18T18:00:00',
            'subtasks': [{
                'title': 'Confirmar catering',
                'target_date': '2026-10-19',
                'estimated_minutes': 60,
            }],
        },
    )

    assert response.status_code == 400
    assert inserted_events
    assert deleted_rows == ['subtasks', 'events']


def test_initial_plan_returns_conflicting_subtask_and_rolls_back(monkeypatch):
    inserted_rows = []
    deleted_rows = []

    def fake_insert_row(table, payload):
        inserted_rows.append((table, payload))
        return payload

    monkeypatch.setattr(api_module, '_insert_row', fake_insert_row)
    monkeypatch.setattr(api_module, '_delete_rows', lambda table, filters: deleted_rows.append(table) or [])
    monkeypatch.setattr(api_module, '_get_row', lambda table, filters, select='*': {'daily_limit_minutes': 360})
    monkeypatch.setattr(
        api_module,
        '_get_rows',
        lambda *args, **kwargs: [{'id': 'existing-task', 'estimated_minutes': 300}],
    )

    response = client.post(
        '/api/eventos/plan-inicial/',
        json={
            'name': 'Plan con conflicto',
            'event_type': 'Boda',
            'event_date': '2026-11-05T18:00:00',
            'user_id': '11111111-1111-1111-1111-111111111111',
            'subtasks': [{
                'title': 'Buscar proveedores',
                'target_date': '2026-10-20',
                'estimated_minutes': 120,
            }],
        },
    )

    assert response.status_code == 409
    assert response.json()['detail']['planned_minutes'] == 420
    assert response.json()['detail']['limit_minutes'] == 360
    assert response.json()['detail']['subtask_index'] == 0
    assert response.json()['detail']['subtask_title'] == 'Buscar proveedores'
    assert [table for table, _ in inserted_rows] == ['events']
    assert deleted_rows == ['subtasks', 'events']


def test_reprogram_subtask_rejects_dates_after_event_and_detects_capacity(monkeypatch):
    event_id = '550e8400-e29b-41d4-a716-446655440000'
    subtask_id = '7c9e6679-7425-40de-944b-e07fc1f90ae7'
    current = {
        'id': subtask_id,
        'event_id': event_id,
        'title': 'Buscar proveedores',
        'target_date': '2026-10-18',
        'estimated_minutes': 60,
        'status': 'pending',
        'created_at': '2026-10-01T00:00:00+00:00',
    }
    updated_rows = []
    queried_rows = []

    def fake_get_row(table, filters, select='*'):
        if table == 'subtasks':
            return current
        if table == 'events':
            return {'id': event_id, 'event_date': '2026-11-05T18:00:00', 'user_id': '11111111-1111-1111-1111-111111111111'}
        if table == 'users':
            return {'daily_limit_minutes': 360}
        return None

    def fake_get_rows(table, filters=None, select='*'):
        queried_rows.append((table, filters, select))
        return [{'id': 'other-task', 'estimated_minutes': 300}]

    def fake_update_row(table, filters, payload):
        updated_rows.append(payload)
        return {**current, **payload}

    monkeypatch.setattr(api_module, '_get_row', fake_get_row)
    monkeypatch.setattr(api_module, '_get_rows', fake_get_rows)
    monkeypatch.setattr(api_module, '_update_row', fake_update_row)

    conflict_response = client.patch(
        f'/api/eventos/{event_id}/subtareas/{subtask_id}',
        json={'target_date': '2026-10-20', 'estimated_minutes': 120},
    )
    success_response = client.patch(
        f'/api/eventos/{event_id}/subtareas/{subtask_id}',
        json={'target_date': '2026-10-20', 'estimated_minutes': 60},
    )
    invalid_date_response = client.patch(
        f'/api/eventos/{event_id}/subtareas/{subtask_id}',
        json={'target_date': '2026-11-06'},
    )

    assert conflict_response.status_code == 409
    assert conflict_response.json()['detail'] == {
        'code': 'daily_capacity_exceeded',
        'target_date': '2026-10-20',
        'planned_minutes': 420,
        'limit_minutes': 360,
        'subtask_id': subtask_id,
    }
    assert success_response.status_code == 200
    assert success_response.json()['target_date'] == '2026-10-20'
    assert invalid_date_response.status_code == 400
    assert len(updated_rows) == 1
    assert queried_rows


def test_event_date_cannot_move_before_existing_subtask_deadline(monkeypatch):
    event_id = '550e8400-e29b-41d4-a716-446655440000'
    updates = []
    monkeypatch.setattr(
        api_module,
        '_get_row',
        lambda table, filters, select='*': {'id': event_id, 'event_date': '2026-11-05T18:00:00'},
    )
    monkeypatch.setattr(
        api_module,
        '_get_rows',
        lambda *args, **kwargs: [{'id': 'task-1', 'target_date': '2026-10-20'}],
    )
    monkeypatch.setattr(api_module, '_update_row', lambda *args, **kwargs: updates.append(args) or {})

    response = client.patch(
        f'/api/eventos/{event_id}',
        json={'event_date': '2026-10-15T18:00:00'},
    )

    assert response.status_code == 400
    assert 'fecha límite de una gestión existente' in response.json()['detail']
    assert updates == []


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
