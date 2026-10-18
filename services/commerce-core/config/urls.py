from django.urls import path

from catalog import views as catalog
from common import views as common
from orders import views as orders

admin = "api/commerce/admin"

urlpatterns = [
    path("healthz", common.healthz),
    path("readyz", common.readyz),

    # Shop owner admin API
    path(f"{admin}/products", catalog.AdminProductList.as_view()),
    path(f"{admin}/products/<uuid:product_id>", catalog.AdminProductDetail.as_view()),
    path(f"{admin}/products/<uuid:product_id>/restore", catalog.AdminProductRestore.as_view()),
    path(f"{admin}/inventory/<uuid:product_id>/adjust", catalog.AdminInventoryAdjust.as_view()),
    path(f"{admin}/inventory/<uuid:product_id>/adjustments", catalog.AdminInventoryHistory.as_view()),
    path(f"{admin}/orders", orders.AdminOrderList.as_view()),
    path(f"{admin}/dashboard", orders.AdminDashboard.as_view()),

    # Customer commerce API
    path("api/commerce/cart", orders.CartView.as_view()),
    path("api/commerce/checkout", orders.CheckoutView.as_view()),
    path("api/commerce/orders", orders.OrderList.as_view()),
    path("api/commerce/orders/<uuid:order_id>", orders.OrderDetail.as_view()),

    # Public catalog: the database-backed path (baseline, and the fallback
    # contract for catalog-reader).
    path("api/catalog/products", catalog.PublicProductList.as_view()),
    path("api/catalog/products/<uuid:product_id>", catalog.PublicProductDetail.as_view()),
    path("api/catalog/categories", catalog.PublicCategoryList.as_view()),
    path("api/catalog/search", catalog.PublicProductList.as_view()),
]
