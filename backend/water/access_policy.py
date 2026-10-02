from dataclasses import dataclass
from enum import StrEnum


class ScopeType(StrEnum):
    SELF = "self"
    ACCOUNT = "account"
    LAND_PLOT = "land_plot"
    WATER_GROUP = "water_group"
    SUPPLY_NODE = "supply_node"
    PERSON = "person"
    ALL = "all"


@dataclass(frozen=True)
class CapabilitySpec:
    code: str
    label: str
    scopes: tuple[ScopeType, ...]
    delegable: bool = False
    require_mfa: bool = False
    maker_checker: bool = False


@dataclass(frozen=True)
class RoleTemplate:
    code: str
    version: int
    label: str
    scopes: tuple[ScopeType, ...]
    capabilities: tuple[str, ...]


def cap(code, label, scopes, *, delegable=False, require_mfa=False, maker_checker=False):
    return CapabilitySpec(
        code=code,
        label=label,
        scopes=tuple(scopes),
        delegable=delegable,
        require_mfa=require_mfa,
        maker_checker=maker_checker,
    )


S = ScopeType

_CAPS = [
    cap("resident.account.view", "Личный доступ к данным счёта", (S.ACCOUNT,), delegable=True),
    cap("resident.finance.view", "Просмотр своих начислений и оплат", (S.ACCOUNT,), delegable=True),
    cap("resident.water.submit", "Передача показаний по своему счёту", (S.ACCOUNT,), delegable=True),
    cap("resident.documents.view", "Просмотр документов своего счёта", (S.ACCOUNT,), delegable=True),
    cap("resident.appeals.use", "Свои обращения по счёту", (S.ACCOUNT,), delegable=True),
    cap("resident.represent", "Представительские действия по счёту", (S.ACCOUNT,), delegable=True),

    cap("accounts.view", "Просмотр лицевых счетов", (S.ACCOUNT, S.WATER_GROUP, S.SUPPLY_NODE, S.ALL)),
    cap("accounts.edit", "Изменение лицевых счетов", (S.ACCOUNT, S.ALL), require_mfa=True),
    cap("accounts.export", "Экспорт лицевых счетов", (S.ACCOUNT, S.ALL), require_mfa=True),
    cap("plots.view", "Просмотр участков", (S.LAND_PLOT, S.ACCOUNT, S.ALL)),
    cap("plots.edit", "Изменение участков", (S.LAND_PLOT, S.ALL), require_mfa=True),
    cap("relations.view", "Просмотр связей человек–участок", (S.PERSON, S.LAND_PLOT, S.ALL)),
    cap("relations.edit", "Изменение связей человек–участок", (S.PERSON, S.LAND_PLOT, S.ALL), require_mfa=True),

    cap("water.view", "Просмотр водного раздела", (S.ACCOUNT, S.WATER_GROUP, S.SUPPLY_NODE, S.ALL)),
    cap("water.meters.view", "Просмотр счётчиков", (S.ACCOUNT, S.WATER_GROUP, S.SUPPLY_NODE, S.ALL)),
    cap("water.reading.view", "Просмотр журнала показаний", (S.ACCOUNT, S.WATER_GROUP, S.SUPPLY_NODE, S.ALL)),
    cap("water.reading.submit_official", "Внесение официальных показаний", (S.WATER_GROUP, S.SUPPLY_NODE, S.ALL)),
    cap("water.line_submission.submit", "Подача показаний старшим линии на проверку", (S.WATER_GROUP,)),
    cap("water.observation.submit", "Контрольное наблюдение", (S.WATER_GROUP, S.SUPPLY_NODE, S.ALL)),
    cap("water.observation.review_line", "Проверка показаний жителей старшим линии", (S.WATER_GROUP,)),
    cap("water.observation.finalize", "Финальная модерация показаний", (S.SUPPLY_NODE, S.ALL), require_mfa=True, maker_checker=True),
    cap("water.balance.view", "Просмотр баланса и потерь", (S.WATER_GROUP, S.SUPPLY_NODE, S.ALL)),
    cap("water.topology.view", "Просмотр состава линий", (S.ACCOUNT, S.WATER_GROUP, S.SUPPLY_NODE, S.ALL)),
    cap("water.topology.manage", "Изменение топологии воды", (S.SUPPLY_NODE, S.ALL), require_mfa=True),
    cap("water.group_consumption.manage", "Учёт расхода линии", (S.WATER_GROUP, S.ALL), require_mfa=True),
    cap("water.export", "Экспорт водных данных", (S.WATER_GROUP, S.SUPPLY_NODE, S.ALL), require_mfa=True),
    cap("water.history.correct", "Исключительная коррекция истории воды", (S.ALL,), require_mfa=True, maker_checker=True),

    cap("finance.view", "Просмотр финансов", (S.ACCOUNT, S.ALL), require_mfa=True),
    cap("finance.period.calculate", "Расчёт периода", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("finance.charge.approve", "Утверждение начисления", (S.ACCOUNT, S.ALL), require_mfa=True, maker_checker=True),
    cap("finance.charge.cancel", "Отмена черновика начисления", (S.ACCOUNT, S.ALL), require_mfa=True),
    cap("finance.period.approve", "Утверждение расчётного периода", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("finance.period.close", "Закрытие расчётного периода", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("finance.payment.create", "Ввод оплаты", (S.ACCOUNT, S.ALL), require_mfa=True, maker_checker=True),
    cap("finance.payment.confirm", "Подтверждение оплаты", (S.ACCOUNT, S.ALL), require_mfa=True, maker_checker=True),
    cap("finance.payment.reverse", "Отмена подтверждённой оплаты", (S.ACCOUNT, S.ALL), require_mfa=True, maker_checker=True),
    cap("finance.payment.allocate", "Распределение оплаты", (S.ACCOUNT, S.ALL), require_mfa=True),
    cap("finance.policy.manage", "Тарифы и правила начислений", (S.ALL,), require_mfa=True),
    cap("finance.export", "Экспорт финансов", (S.ACCOUNT, S.ALL), require_mfa=True),

    cap("appeals.view", "Просмотр обращений", (S.ACCOUNT, S.ALL)),
    cap("appeals.reply", "Ответ на обращение", (S.ACCOUNT, S.ALL)),
    cap("appeals.status.change", "Изменение статуса обращения", (S.ACCOUNT, S.ALL)),
    cap("appeals.close", "Закрытие обращения", (S.ACCOUNT, S.ALL)),
    cap("appeals.attachment.view", "Просмотр вложений обращения", (S.ACCOUNT, S.ALL)),
    cap("appeals.attachment.manage", "Работа с вложениями обращения", (S.ACCOUNT, S.ALL)),

    cap("documents.account.view", "Просмотр документов счёта", (S.ACCOUNT, S.ALL)),
    cap("documents.account.create", "Добавление документа счёта", (S.ACCOUNT, S.ALL), require_mfa=True),
    cap("documents.account.edit_metadata", "Изменение реквизитов документа счёта", (S.ACCOUNT, S.ALL), require_mfa=True),
    cap("documents.account.download", "Скачивание документа счёта", (S.ACCOUNT, S.ALL)),
    cap("documents.public.view", "Просмотр публичных документов", (S.ALL,)),
    cap("documents.public.create", "Создание публичного документа", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("documents.public.edit", "Редактирование публичного документа", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("documents.public.publish", "Публикация публичного документа", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("news.view", "Просмотр новостей в редакторе", (S.ALL,)),
    cap("news.create", "Создание новости", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("news.edit", "Редактирование новости", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("news.publish", "Публикация новости", (S.ALL,), require_mfa=True, maker_checker=True),

    cap("access.view", "Просмотр центра доступов", (S.PERSON, S.ACCOUNT, S.ALL), require_mfa=True),
    cap("access.request.review", "Проверка заявки на доступ", (S.ALL,), require_mfa=True),
    cap("access.request.decide", "Решение по заявке на доступ", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("access.identity.verify", "Подтверждение личности", (S.PERSON, S.ALL), require_mfa=True),
    cap("access.person.create", "Создание карточки человека", (S.ALL,), require_mfa=True),
    cap("access.invite.issue", "Выдача приглашения", (S.PERSON, S.ACCOUNT, S.ALL), require_mfa=True),
    cap("access.invite.revoke", "Отзыв приглашения", (S.PERSON, S.ACCOUNT, S.ALL), require_mfa=True),
    cap("access.grant.view", "Просмотр выданных прав", (S.PERSON, S.ACCOUNT, S.ALL), require_mfa=True),
    cap("access.grant.issue", "Выдача прав", (S.PERSON, S.ACCOUNT, S.ALL), require_mfa=True, maker_checker=True),
    cap("access.grant.edit", "Изменение набора личных прав", (S.PERSON, S.ACCOUNT, S.ALL), require_mfa=True, maker_checker=True),
    cap("access.grant.end", "Завершение прав", (S.PERSON, S.ACCOUNT, S.ALL), require_mfa=True),
    cap("access.password_reset.issue", "Одноразовое восстановление доступа", (S.PERSON, S.ALL), require_mfa=True),
    cap("access.password_reset.revoke", "Отзыв восстановления доступа", (S.PERSON, S.ALL), require_mfa=True),
    cap("access.assignment.manage", "Управление служебными назначениями", (S.PERSON, S.ALL), require_mfa=True, maker_checker=True),
    cap("access.delegation.review", "Проверка делегированных полномочий", (S.PERSON, S.ACCOUNT, S.ALL), require_mfa=True),
    cap("access.audit.view", "Просмотр истории полномочий", (S.PERSON, S.ACCOUNT, S.ALL), require_mfa=True),

    cap("registry.view", "Просмотр закрытого реестра", (S.PERSON, S.ALL), require_mfa=True),
    cap("registry.contacts.view", "Просмотр закрытых контактов", (S.PERSON, S.ALL), require_mfa=True),
    cap("registry.edit", "Изменение закрытого реестра", (S.PERSON, S.ALL), require_mfa=True),
    cap("registry.export", "Экспорт закрытого реестра", (S.ALL,), require_mfa=True),

    cap("governance.board.view", "Просмотр материалов правления", (S.SELF, S.ALL)),
    cap("governance.poll.create", "Создание опроса правления", (S.ALL,), require_mfa=True),
    cap("governance.poll.edit", "Изменение опроса правления", (S.ALL,), require_mfa=True),
    cap("governance.poll.close", "Закрытие опроса правления", (S.ALL,), require_mfa=True),
    cap("governance.protocol.add", "Добавление протокола", (S.ALL,), require_mfa=True),
    cap("governance.audit.view", "Просмотр аудита правления", (S.ALL,), require_mfa=True),
    cap("governance.membership.manage", "Управление составом правления", (S.PERSON, S.ALL), require_mfa=True, maker_checker=True),

    cap("security.alert.view", "Просмотр сигналов безопасности", (S.ALL,), require_mfa=True),
    cap("security.alert.review", "Обработка сигнала безопасности", (S.ALL,), require_mfa=True, maker_checker=True),

    cap("system.import.stage", "Предварительный импорт", (S.ALL,), require_mfa=True),
    cap("system.import.apply", "Применение импорта", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("system.user.manage", "Управление логинами", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("system.mfa.manage", "Управление MFA", (S.ALL,), require_mfa=True, maker_checker=True),
    cap("system.django_admin", "Технический Django Admin", (S.ALL,), require_mfa=True),
    cap("system.break_glass", "Аварийный полный доступ", (S.ALL,), require_mfa=True),
]

CAPABILITIES = {item.code: item for item in _CAPS}


def role(code, version, label, scopes, capabilities):
    return RoleTemplate(code, version, label, tuple(scopes), tuple(capabilities))


ROLE_TEMPLATES = {
    item.code: item
    for item in [
        role("resident_account", 1, "Житель / доступ к счёту", (S.ACCOUNT,), (
            "resident.account.view", "resident.water.submit",
        )),
        role("line_senior", 1, "Старший линии", (S.WATER_GROUP,), (
            "accounts.view", "water.view", "water.meters.view", "water.reading.view",
            "water.topology.view", "water.line_submission.submit",
            "water.observation.review_line", "water.balance.view",
        )),
        role("line_deputy", 1, "Заместитель старшего линии", (S.WATER_GROUP,), (
            "accounts.view", "water.view", "water.meters.view", "water.reading.view",
            "water.topology.view", "water.line_submission.submit",
            "water.observation.review_line", "water.balance.view",
        )),
        role("controller", 1, "Контролёр", (S.WATER_GROUP, S.SUPPLY_NODE), (
            "accounts.view", "water.view", "water.meters.view", "water.reading.view",
            "water.topology.view", "water.observation.submit", "water.balance.view",
        )),
        role("water_operator", 1, "Оператор воды", (S.SUPPLY_NODE, S.ALL), (
            "accounts.view", "water.view", "water.meters.view", "water.reading.view",
            "water.topology.view", "water.reading.submit_official",
            "water.balance.view", "water.export",
        )),
        role("water_moderator", 1, "Модератор воды", (S.SUPPLY_NODE, S.ALL), (
            "accounts.view", "water.view", "water.meters.view", "water.reading.view",
            "water.topology.view", "water.observation.finalize", "water.balance.view",
            "water.export",
        )),
        role("finance_viewer", 1, "Финансы — просмотр", (S.ALL,), ("finance.view",)),
        role("cashier", 1, "Кассир", (S.ALL,), (
            "finance.view", "finance.payment.create", "finance.payment.allocate",
        )),
        role("accountant", 1, "Бухгалтер", (S.ALL,), (
            "finance.view", "finance.period.calculate", "finance.charge.approve",
            "finance.charge.cancel", "finance.period.approve", "finance.period.close",
            "finance.payment.create", "finance.payment.confirm", "finance.payment.reverse",
            "finance.payment.allocate", "finance.export",
        )),
        role("appeals_operator", 1, "Обращения", (S.ALL,), (
            "appeals.view", "appeals.reply", "appeals.status.change", "appeals.close",
            "appeals.attachment.view", "appeals.attachment.manage",
        )),
        role("account_documents", 1, "Документы лицевых счетов", (S.ALL,), (
            "documents.account.view", "documents.account.create",
            "documents.account.edit_metadata", "documents.account.download",
        )),
        role("public_content_editor", 1, "Редактор публичных материалов", (S.ALL,), (
            "documents.public.view", "documents.public.create", "documents.public.edit",
            "news.view", "news.create", "news.edit",
        )),
        role("public_content_publisher", 1, "Публикатор", (S.ALL,), (
            "documents.public.view", "documents.public.publish", "news.view", "news.publish",
        )),
        role("private_registry", 1, "Закрытый реестр", (S.ALL,), (
            "registry.view", "registry.contacts.view", "registry.edit",
            "relations.view", "relations.edit",
        )),
        role("access_admin", 1, "Управление доступами", (S.ALL,), (
            "access.view", "access.request.review", "access.request.decide",
            "access.identity.verify", "access.person.create", "access.invite.issue",
            "access.invite.revoke", "access.grant.view", "access.grant.issue", "access.grant.edit",
            "access.grant.end", "access.password_reset.issue", "access.password_reset.revoke",
            "access.delegation.review", "access.audit.view",
        )),
        role("governance_secretary", 1, "Правление — секретарь", (S.ALL,), (
            "governance.board.view", "governance.poll.create", "governance.poll.edit",
            "governance.poll.close", "governance.protocol.add", "governance.audit.view",
            "governance.membership.manage",
        )),
        role("auditor", 1, "Ревизор / аудитор", (S.ALL,), (
            "accounts.view", "plots.view", "water.view", "water.meters.view",
            "water.reading.view", "water.balance.view", "water.export",
            "finance.view", "finance.export", "documents.account.view",
            "documents.account.download", "documents.public.view",
            "access.audit.view", "governance.board.view", "governance.audit.view",
        )),
        role("tsn_admin", 1, "Администратор ТСН", (S.ALL,), (
            "accounts.view", "accounts.edit", "accounts.export", "plots.view", "plots.edit",
            "relations.view", "relations.edit", "water.view", "water.meters.view",
            "water.reading.view", "water.reading.submit_official", "water.observation.submit",
            "water.observation.finalize", "water.balance.view", "water.topology.view",
            "water.topology.manage", "water.group_consumption.manage", "water.export",
            "finance.view", "finance.period.calculate", "finance.charge.approve",
            "finance.charge.cancel", "finance.period.approve", "finance.period.close",
            "finance.payment.create", "finance.payment.confirm", "finance.payment.reverse",
            "finance.payment.allocate", "finance.policy.manage", "finance.export",
            "appeals.view", "appeals.reply", "appeals.status.change", "appeals.close",
            "appeals.attachment.view", "appeals.attachment.manage",
            "documents.account.view", "documents.account.create",
            "documents.account.edit_metadata", "documents.account.download",
            "documents.public.view", "documents.public.create", "documents.public.edit",
            "documents.public.publish", "news.view", "news.create", "news.edit", "news.publish",
            "access.view", "access.request.review", "access.request.decide",
            "access.identity.verify", "access.person.create", "access.invite.issue",
            "access.invite.revoke", "access.grant.view", "access.grant.issue",
            "access.grant.edit", "access.grant.end", "access.password_reset.issue",
            "access.password_reset.revoke", "access.assignment.manage",
            "access.delegation.review", "access.audit.view",
            "registry.view", "registry.contacts.view", "registry.edit", "registry.export",
            "governance.board.view", "governance.poll.create", "governance.poll.edit",
            "governance.poll.close", "governance.protocol.add", "governance.audit.view",
            "governance.membership.manage",
            "security.alert.view", "security.alert.review",
            "system.import.stage", "system.import.apply",
        )),
    ]
}


def _validate_registry():
    if len(CAPABILITIES) != len(_CAPS):
        raise RuntimeError("Duplicate access capability code")
    for template in ROLE_TEMPLATES.values():
        for code in template.capabilities:
            spec = CAPABILITIES.get(code)
            if spec is None:
                raise RuntimeError(f"Unknown capability {code!r} in role {template.code!r}")
            if not set(template.scopes).intersection(spec.scopes):
                raise RuntimeError(
                    f"Role {template.code!r} has no compatible scope for capability {code!r}"
                )


_validate_registry()
