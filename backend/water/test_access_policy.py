from django.test import SimpleTestCase

from .access_policy import CAPABILITIES, ROLE_TEMPLATES, ScopeType


class AccessPolicyRegistryTests(SimpleTestCase):
    def test_every_role_capability_exists_and_has_compatible_scope(self):
        for role in ROLE_TEMPLATES.values():
            self.assertGreater(role.version, 0)
            self.assertTrue(role.scopes)
            self.assertTrue(role.capabilities)
            for code in role.capabilities:
                spec = CAPABILITIES[code]
                self.assertTrue(set(role.scopes).intersection(spec.scopes), (role.code, code))

    def test_line_senior_and_controller_are_distinct(self):
        senior = ROLE_TEMPLATES["line_senior"]
        controller = ROLE_TEMPLATES["controller"]
        self.assertIn("water.observation.review_line", senior.capabilities)
        self.assertNotIn("water.observation.review_line", controller.capabilities)
        self.assertIn("water.observation.submit", controller.capabilities)
        self.assertNotIn("water.observation.submit", senior.capabilities)

    def test_line_roles_do_not_leak_finance_registry_or_access_admin(self):
        forbidden_prefixes = ("finance.", "registry.", "access.")
        for name in ("line_senior", "line_deputy", "controller"):
            for code in ROLE_TEMPLATES[name].capabilities:
                self.assertFalse(code.startswith(forbidden_prefixes), (name, code))

    def test_only_personal_account_capabilities_are_delegable_in_foundation(self):
        delegable = {code for code, spec in CAPABILITIES.items() if spec.delegable}
        self.assertEqual(
            delegable,
            {
                "resident.account.view",
                "resident.finance.view",
                "resident.water.submit",
                "resident.documents.view",
                "resident.appeals.use",
                "resident.represent",
            },
        )

    def test_sensitive_domains_require_mfa(self):
        for prefix in ("finance.", "registry.", "access.", "security.", "system."):
            for code, spec in CAPABILITIES.items():
                if code.startswith(prefix):
                    self.assertTrue(spec.require_mfa, code)

    def test_resident_preset_is_account_scoped(self):
        role = ROLE_TEMPLATES["resident_account"]
        self.assertEqual(role.scopes, (ScopeType.ACCOUNT,))
        self.assertNotIn("resident.finance.view", role.capabilities)
        self.assertNotIn("resident.documents.view", role.capabilities)
        self.assertNotIn("resident.represent", role.capabilities)

    def test_cashier_and_accountant_keep_confirmation_separate(self):
        self.assertNotIn("finance.payment.confirm", ROLE_TEMPLATES["cashier"].capabilities)
        self.assertIn("finance.payment.confirm", ROLE_TEMPLATES["accountant"].capabilities)

    def test_editor_does_not_publish_and_publisher_does_not_edit(self):
        editor = ROLE_TEMPLATES["public_content_editor"].capabilities
        publisher = ROLE_TEMPLATES["public_content_publisher"].capabilities
        self.assertNotIn("documents.public.publish", editor)
        self.assertNotIn("news.publish", editor)
        self.assertNotIn("documents.public.edit", publisher)
        self.assertNotIn("news.edit", publisher)
