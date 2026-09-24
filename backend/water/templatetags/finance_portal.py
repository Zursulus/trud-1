from django import template

from water.finance_reporting import account_charge_rows


register = template.Library()


@register.simple_tag
def resident_charge_rows(account):
    return account_charge_rows(account)
