from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from openhands.sdk.skills import Skill
from storage.agent_profile_resolution import org_launch_stores


def test_org_launch_stores_materializes_only_public_skill_catalog():
    public_skill = Skill(
        name='public-skill',
        content='public',
        source='/public/SKILL.md',
    )
    process_user_skill = Skill(
        name='server-account-user-skill',
        content='private',
        source='/server-account/.openhands/skills/private/SKILL.md',
    )
    org = SimpleNamespace(
        id=uuid4(),
        llm_profiles=None,
        provider_connections=None,
    )

    with (
        patch(
            'storage.agent_profile_resolution.load_public_skills',
            return_value=[public_skill],
        ) as load_public,
        patch(
            'openhands.agent_server.skills_service.discover_profile_skills',
            return_value=[public_skill, process_user_skill],
        ) as discover,
    ):
        stores = org_launch_stores(org, {})
        assert stores.skills() == [public_skill]
        assert stores.public_skill_names is not None
        assert stores.public_skill_names() == ('public-skill',)

    load_public.assert_called_once_with()
    discover.assert_not_called()
