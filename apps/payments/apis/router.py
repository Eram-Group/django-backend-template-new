"""The payments app's public router - mounted at /payments/."""

from ninja import Router

from apps.payments.apis.cards import router as cards_router
from apps.payments.apis.payments import router as payments_router
from apps.payments.apis.wallets import router as wallets_router
from apps.payments.apis.webhooks import router as webhooks_router

# Order matters: static paths (/wallet, /webhooks/..., /cards) must register
# before payments' catch-all /{payment_id}.
router = Router()
router.add_router("", wallets_router)
router.add_router("", webhooks_router)
router.add_router("", cards_router)
router.add_router("", payments_router)
