from django.urls import path
from stock import views
from django.views.generic import TemplateView

urlpatterns = [
    path("login/", views.sign_in, name="login"),
    path("logout/", views.sign_out, name="logout"),
    path("", views.stock_list, name="stock"),
    path("history/", views.history, name="history"),
    path("rotation/", views.rotation, name="rotation"),
    path("attention/", views.attention, name="attention"),
    path("out-of-stock/", views.out_of_stock, name="out_of_stock"),
    path("categories/", views.category_list, name="categories"),
    path("categories/add/", views.category_edit, name="category_add"),
    path("categories/<int:pk>/edit/", views.category_edit, name="category_edit"),
    path("categories/<int:pk>/delete/", views.category_delete, name="category_delete"),
    path("add/", views.add_stock, name="add"),
    path("catalogue/search/", views.search, name="search"),
    path("catalogue/lookup/", views.product_lookup, name="lookup"),
    path("images/<slug:digest>/", views.product_image, name="image"),
    path(
        "credits/",
        TemplateView.as_view(
            template_name="credits.html", extra_context={"title": "Sources and licences"}
        ),
        name="credits",
    ),
    path("products/<int:pk>/", views.product_detail, name="product"),
    path("products/<int:pk>/photo/", views.edit_photo, name="edit_photo"),
    path("products/<int:pk>/photo/remove/", views.remove_photo, name="remove_photo"),
    path("products/<int:pk>/edit/", views.edit_product, name="edit"),
    path("products/<int:pk>/use/", views.use_one, name="use_one"),
    path("products/<int:pk>/increase/", views.increase_one, name="increase_one"),
    path("products/<int:pk>/delete/", views.delete_product, name="delete"),
    path("batches/<int:pk>/adjust/", views.adjust, name="adjust"),
    path("batches/<int:pk>/edit/", views.edit_batch, name="edit_batch"),
    path("changes/<int:pk>/undo/", views.undo, name="undo"),
]
