"""ACL helpers for graph — pure logic + mock-free document visibility rules."""

from __future__ import annotations

from apps.api.services.graph.acl import can_mutate_graph
from apps.api.auth import AuthUser
import uuid


def test_only_admin_mutates_graph():
    admin = AuthUser(id=uuid.uuid4(), login="a", tenant_id=uuid.uuid4(), role="admin")
    member = AuthUser(id=uuid.uuid4(), login="m", tenant_id=uuid.uuid4(), role="member")
    assert can_mutate_graph(admin) is True
    assert can_mutate_graph(member) is False
