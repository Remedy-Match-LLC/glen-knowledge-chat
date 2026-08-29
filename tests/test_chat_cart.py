from dashboard.chat_cart import explicit_cart_items


CATALOG = [
    {"slug": "brain-boost", "name": "Brain Boost"},
    {"slug": "man-manna", "name": "Man Manna"},
    {"slug": "terrain-restore", "name": "Terrain Restore"},
]


def test_explicit_multi_product_command_with_quantities():
    assert explicit_cart_items(
        "Please add two Brain Boost and 3 bottles of Man Manna to my order", CATALOG
    ) == [
        {"slug": "brain-boost", "name": "Brain Boost", "qty": 2},
        {"slug": "man-manna", "name": "Man Manna", "qty": 3},
    ]


def test_question_or_recommendation_context_never_mutates_cart():
    assert explicit_cart_items("What does Brain Boost do?", CATALOG) == []
    assert explicit_cart_items("Do you recommend Terrain Restore?", CATALOG) == []
    assert explicit_cart_items("Do I need Brain Boost?", CATALOG) == []


def test_negated_order_command_never_mutates_cart():
    assert explicit_cart_items("Don't add Brain Boost", CATALOG) == []


def test_slug_and_quantity_cap_are_supported():
    assert explicit_cart_items("order 500 brain-boost", CATALOG) == [
        {"slug": "brain-boost", "name": "Brain Boost", "qty": 99}]
