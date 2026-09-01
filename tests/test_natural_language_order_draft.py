from decimal import Decimal

from app.services.natural_language_order_draft import NaturalLanguageOrderDraftService


def test_parse_purchase_intent_extracts_quoted_product_and_wan_amount():
    result = NaturalLanguageOrderDraftService.parse_purchase_intent('我要申购“稳健精选基金”10万元')
    assert result.product_name == '稳健精选基金'
    assert result.amount == Decimal('100000')


def test_parse_purchase_intent_extracts_product_before_action():
    result = NaturalLanguageOrderDraftService.parse_purchase_intent('稳健精选基金申购5000元')
    assert result.product_name == '稳健精选基金'
    assert result.amount == Decimal('5000')


def test_parse_purchase_intent_does_not_guess_missing_values():
    result = NaturalLanguageOrderDraftService.parse_purchase_intent('我要申购')
    assert result.product_name is None
    assert result.amount is None


def test_confirmation_token_hash_is_deterministic_and_not_plaintext():
    token = "test-confirmation-token-which-is-long-enough"
    digest = NaturalLanguageOrderDraftService._token_hash(token)
    assert digest != token
    assert len(digest) == 64
    assert digest == NaturalLanguageOrderDraftService._token_hash(token)


def test_fingerprint_changes_when_order_parameters_change():
    base = NaturalLanguageOrderDraftService._fingerprint(
        customer_id="customer-1", product_id="product-1", amount=Decimal("1000.00"), version=1
    )
    changed_amount = NaturalLanguageOrderDraftService._fingerprint(
        customer_id="customer-1", product_id="product-1", amount=Decimal("2000.00"), version=1
    )
    changed_version = NaturalLanguageOrderDraftService._fingerprint(
        customer_id="customer-1", product_id="product-1", amount=Decimal("1000.00"), version=2
    )
    assert base != changed_amount
    assert base != changed_version


def test_parse_purchase_intent_extracts_amount_before_product_after_action():
    result = NaturalLanguageOrderDraftService.parse_purchase_intent('我要申购1000元现金管理保本计划')
    assert result.product_name == '现金管理保本计划'
    assert result.amount == Decimal('1000')

def test_parse_purchase_intent_extracts_product_before_amount_after_action():
    result = NaturalLanguageOrderDraftService.parse_purchase_intent('我要申购现金管理保本计划1000元')
    assert result.product_name == '现金管理保本计划'
    assert result.amount == Decimal('1000')
