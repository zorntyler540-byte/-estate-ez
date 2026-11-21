"""Snapshot of seller.ctbids.com GET /category/list names (exact spelling)."""

from __future__ import annotations

CT_SELLER_CATEGORIES: frozenset[str] = frozenset(
    {
        'Accessories',
        'Advertising',
        'All',
        'Animals',
        'Animation Art & Characters',
        'Antique',
        'Antique & Vintage',
        'Antiques',
        'Antiques Other',
        'Appliances',
        'Art',
        'Art Other',
        'Artful Pottery',
        'Asian Art',
        'Audio',
        'Baby',
        'Baby Asseccories',
        'Baby accessories',
        'Bar ware & Breweriana',
        'Bath',
        'Bedroom',
        'Bedroom Furniture',
        'Bedroom Goods',
        'Boats',
        'Book Shelf',
        'Books',
        'Books & Periodicals',
        'Bottles & Insulators',
        'Bracelets',
        'Building Materials',
        'Cabinets',
        'Camera Other',
        'Cameras & Photo Equipment',
        'Camping',
        'Car Seat',
        'Carpets & Rugs',
        'Cars',
        'Chairs',
        'Children Footwear',
        'Children Toys',
        'Children cloths',
        'Childrens',
        'China Sets',
        'Clocks',
        'Clothing & Accessories',
        'Clothing Other',
        'Coins',
        'Collectibles',
        'Collectibles Other',
        'Comics',
        'Computers, Laptops',
        'Costume Jewelry',
        'Craft & Hobby',
        'Crafts & Hobbies',
        'Crystal',
        'Cycling',
        'DVDs',
        'Decor',
        'Decorative Art',
        'Decorative Collectibles',
        'Desks',
        'Digital',
        'Dining Room',
        'Dolls',
        'Earrings',
        'Electronics',
        'Electronics Other',
        'Farm Equipment',
        'Farm Equipment Other',
        'Figures & Figurines',
        'Film',
        'Fine Jewelry',
        'Fishing',
        'Fitness',
        'Floor Coverings',
        'Folk Art',
        'Footwear',
        'Furniture',
        'Furniture Other',
        'Games',
        'Games & Toys',
        'Glassware',
        'Golf',
        'Grilling & BBQ',
        'Hand Tools',
        'Handbags & Wallets',
        'Hardback',
        'Heavy Equipment',
        'Historical',
        'Holiday & Seasonal',
        'Home Audio',
        'Home Video',
        'Household Goods',
        'Household Goods Other',
        'Household Items',
        'Hunting',
        'Instruments',
        'Jewelry',
        'Jewelry Other',
        'Keyboards & Pianos',
        'Kitchen',
        'Kitchen & Home',
        'Kitchen Appliances',
        'Kitchen Electricals',
        'Kitchen Utensils',
        'Knives, Swords, Blades',
        'Ladders',
        'Lawn & Landscape',
        'Lighting',
        'Linens',
        'Luggage',
        'Luxury and Designer Items',
        'Magazines & Newspapers',
        'Matresses',
        'Medical Equipment',
        "Men's",
        "Men's Jewelry",
        'Mexican Art',
        'Militaria',
        'Mirrors',
        'Mobile Devices',
        'Model Cars',
        'Motor Vehicles',
        'Motor Vehicles Other',
        'Motorcycles',
        'Movie Memorabilia',
        'Music',
        'Music Other',
        'Mystery Lots',
        'Native American Art',
        'Necklaces',
        'Office',
        'Oil Paintings',
        'Paint & Painting Supplies',
        'Paintings',
        'Paperback',
        'Parts & Attachments',
        'Parts and Accessories',
        'Patio & Deck',
        'Patio Furniture',
        'Pet Supplies',
        'Photo Frames',
        'Photography',
        'Power tools',
        'Prints',
        'Railroad/Transportation',
        'Records & CDs',
        'Rings',
        'Rocks, Fossils, Minerals',
        'Sailing',
        'Sculpture',
        'Seasonal & Holiday',
        'Shelving',
        'Shoes',
        'Silver',
        'Sofas',
        'Sporting Goods Other',
        'Sports & Hobbies',
        'Sports Memorabilia',
        'Stamps',
        'Sterling Silver',
        'Storage',
        'Storage & Organization',
        'Sunglasses & Glasses',
        'TVs',
        'Tables',
        'Test Category',
        'Tool Storage',
        'Tools & Home Improvement',
        'Tools - Other',
        'Toys',
        'Toys Other',
        'Tractors',
        'Trading Cards',
        'Trailers',
        'Trains',
        'Trucks',
        'Video',
        'Video Games',
        'Vintage',
        'Vintage & Antique Toys',
        'Vintage Books',
        'Vintage Records',
        'Vintage, Retro, Mid-Century',
        'Vinyl Records',
        'Watches',
        'Watercolor',
        "Women's",
    }
)

CT_SELLER_CATEGORY_DEFAULT = "Household Goods Other"

# Map common misspellings / our older labels onto exact CT Seller names.
_CATEGORY_ALIASES: dict[str, str] = {
    "luxury & designer items": "Luxury and Designer Items",
    "luxury and designer items": "Luxury and Designer Items",
    "kitchen and home": "Kitchen & Home",
    "figures and figurines": "Figures & Figurines",
    "handbags and wallets": "Handbags & Wallets",
    "art supplies": "Art",
    "housewares": "Kitchen & Home",
    "furniture": "Household Goods Other",
    "jewelry": "Jewelry Other",
}


def _norm_cat(value: str) -> str:
    return " ".join((value or "").strip().lower().replace("&", "and").split())


def canonicalize_ct_category(raw: str | None) -> str:
    """
    Return an exact CT Seller category name, or the default catch-all.

    Accepts ``Group#*#Leaf`` SPA form, aliases, and case-insensitive matches against
    the live ``/category/list`` snapshot.
    """
    cat = (raw or "").strip()
    if not cat:
        return CT_SELLER_CATEGORY_DEFAULT

    candidates: list[str] = []
    if "#*#" in cat:
        group, leaf = cat.split("#*#", 1)
        candidates.extend([(leaf or "").strip(), (group or "").strip(), cat])
    else:
        candidates.append(cat)

    for candidate in candidates:
        if not candidate:
            continue
        if candidate in CT_SELLER_CATEGORIES:
            return candidate
        alias = _CATEGORY_ALIASES.get(_norm_cat(candidate))
        if alias and alias in CT_SELLER_CATEGORIES:
            return alias
        want = _norm_cat(candidate)
        for name in CT_SELLER_CATEGORIES:
            if _norm_cat(name) == want:
                return name
    return CT_SELLER_CATEGORY_DEFAULT


# Location “Area” rooms from GET /category/area/list (may vary by franchise).
_DEFAULT_AREAS: tuple[str, ...] = (
    "Barn",
    "Bedroom",
    "Entertainment Cave",
    "Garage",
    "Kids Playroom",
    "Kitchen & Dining",
    "Laundry Storage",
    "Living Room",
    "Office",
    "Patio & Lawn",
)


def infer_ct_area(category: str | None, allowed_areas: list[str] | None = None) -> str:
    """
    Pick a CT Seller Area for a category (SPA auto-fills Area when Category is chosen).

    Clamped to ``allowed_areas`` from ``/category/area/list`` when provided.
    """
    allowed = [a.strip() for a in (allowed_areas or list(_DEFAULT_AREAS)) if (a or "").strip()]
    if not allowed:
        allowed = list(_DEFAULT_AREAS)
    allowed_norm = {_norm_cat(a): a for a in allowed}

    def pick(*candidates: str) -> str | None:
        for c in candidates:
            hit = allowed_norm.get(_norm_cat(c))
            if hit:
                return hit
        return None

    cat = _norm_cat(category or "")
    if any(
        x in cat
        for x in (
            "kitchen",
            "dining",
            "glassware",
            "crystal",
            "china",
            "silver",
            "sterling",
            "bar ware",
            "breweriana",
        )
    ):
        hit = pick("Kitchen & Dining", "Living Room")
        if hit:
            return hit
    if any(
        x in cat
        for x in (
            "women",
            "men",
            "shoe",
            "handbag",
            "wallet",
            "clothing",
            "jewelry",
            "earring",
            "bracelet",
            "ring",
            "necklace",
            "watch",
            "accessory",
            "luggage",
            "bedroom goods",
            "linen",
        )
    ):
        hit = pick("Bedroom", "Living Room")
        if hit:
            return hit
    if any(x in cat for x in ("toy", "doll", "game", "children", "baby", "kids")):
        hit = pick("Kids Playroom", "Bedroom", "Living Room")
        if hit:
            return hit
    if any(x in cat for x in ("tool", "garage", "power tool", "ladder", "building material")):
        hit = pick("Garage", "Barn", "Patio & Lawn")
        if hit:
            return hit
    if any(x in cat for x in ("patio", "lawn", "grill", "outdoor", "garden", "camping", "fishing", "hunting")):
        hit = pick("Patio & Lawn", "Garage", "Barn")
        if hit:
            return hit
    if any(x in cat for x in ("farm", "tractor", "barn")):
        hit = pick("Barn", "Garage", "Patio & Lawn")
        if hit:
            return hit
    if any(
        x in cat
        for x in (
            "music",
            "record",
            "dvd",
            "cd",
            "video game",
            "tv",
            "audio",
            "stereo",
            "instrument",
            "piano",
        )
    ):
        hit = pick("Entertainment Cave", "Living Room", "Office")
        if hit:
            return hit
    if any(
        x in cat
        for x in (
            "electronics",
            "computer",
            "laptop",
            "mobile",
            "camera",
            "photo",
            "office",
            "book",
            "magazine",
            "comic",
        )
    ):
        hit = pick("Office", "Living Room", "Entertainment Cave")
        if hit:
            return hit
    if any(
        x in cat
        for x in (
            "art",
            "paint",
            "print",
            "decor",
            "figurine",
            "antique",
            "sculpture",
            "clock",
            "mirror",
            "lighting",
            "furniture",
            "household",
            "collectible",
        )
    ):
        hit = pick("Living Room", "Kitchen & Dining", "Bedroom")
        if hit:
            return hit

    return pick("Living Room", "Kitchen & Dining", "Bedroom") or allowed[0]


# Curated estate subset for Gemini / appraiser (must ⊆ CT_SELLER_CATEGORIES).
_CT_SELLER_CATEGORIES_FOR_AI_CORE: frozenset[str] = frozenset(
    {
        "Accessories",
        "Antiques Other",
        "Art",
        "Art Other",
        "Bedroom Goods",
        "Books",
        "Bracelets",
        "Cameras & Photo Equipment",
        "China Sets",
        "Clocks",
        "Coins",
        "Comics",
        "Computers, Laptops",
        "Costume Jewelry",
        "Crafts & Hobbies",
        "Crystal",
        "Decor",
        "Decorative Collectibles",
        "Dining Room",
        "Dolls",
        "DVDs",
        "Earrings",
        "Electronics",
        "Electronics Other",
        "Figures & Figurines",
        "Fine Jewelry",
        "Furniture",
        "Furniture Other",
        "Games & Toys",
        "Glassware",
        "Handbags & Wallets",
        "Household Goods Other",
        "Jewelry Other",
        "Kitchen & Home",
        "Lighting",
        "Linens",
        "Luggage",
        "Luxury and Designer Items",
        "Magazines & Newspapers",
        "Men's",
        "Mobile Devices",
        "Music Other",
        "Necklaces",
        "Office",
        "Paintings",
        "Patio & Deck",
        "Photography",
        "Prints",
        "Records & CDs",
        "Rings",
        "Sculpture",
        "Shoes",
        "Sterling Silver",
        "Tools - Other",
        "Toys Other",
        "Video Games",
        "Vintage Books",
        "Watches",
        "Women's",
    }
)

CT_SELLER_CATEGORIES_FOR_AI: frozenset[str] = frozenset(
    name for name in _CT_SELLER_CATEGORIES_FOR_AI_CORE if name in CT_SELLER_CATEGORIES
)


def ct_categories_for_ai() -> list[str]:
    """Sorted CT Seller category names Gemini/appraiser may choose from."""
    return sorted(CT_SELLER_CATEGORIES_FOR_AI)



# Parent group for each live subcategory (GET /category/selection/list, Nov 21 2026).
# item/add sends this as categoryGroup. The seller form always does.
_CT_CATEGORY_GROUP: dict[str, str] = {
    'Accessories': 'Clothing & Accessories',
    'Advertising': 'Collectibles',
    'Ammo': 'Sports & Hobbies',
    'Animals': 'Collectibles',
    'Animation Art & Characters': 'Collectibles',
    'Antique': 'Cameras & Photo Equipment',
    'Antique & Vintage': 'Jewelry',
    'Antiques Other': 'Antiques',
    'Appliances': 'Household Goods',
    'Art Other': 'Art',
    'Artful Pottery': 'Art',
    'Asian Art': 'Art',
    'Audio': 'Books & Periodicals',
    'Baby': 'Household Goods',
    'Barware & Breweriana': 'Collectibles',
    'Bath': 'Household Goods',
    'Bedroom': 'Furniture',
    'Bedroom Goods': 'Household Goods',
    'Boats': 'Motor Vehicles',
    'Bottles & Insulators': 'Collectibles',
    'Bracelets': 'Jewelry',
    'Building Materials': 'Tools & Home Improvement',
    'Cabinets': 'Furniture',
    'Camera Other': 'Cameras & Photo Equipment',
    'Camping': 'Sports & Hobbies',
    'Cars': 'Motor Vehicles',
    'Chairs': 'Furniture',
    'Chest Of Drawers': 'Furniture',
    "Children's": 'Books & Periodicals',
    'China Sets': 'Collectibles',
    'Clocks': 'Collectibles',
    'Clothing Other': 'Clothing & Accessories',
    'Coins': 'Collectibles',
    'Collectibles Other': 'Collectibles',
    'Comics': 'Collectibles',
    'Computers & Laptops': 'Electronics',
    'Costume Jewelry': 'Jewelry',
    'Craft & Hobby': 'Craft & Hobby',
    'Crafts & Hobbies': 'Sports & Hobbies',
    'Crystal': 'Household Goods',
    'Cycling': 'Sports & Hobbies',
    'DVDs': 'Household Goods',
    'Decor': 'Household Goods',
    'Decorative Art': 'Antiques',
    'Decorative Collectibles': 'Collectibles',
    'Desks': 'Furniture',
    'Digital': 'Cameras & Photo Equipment',
    'Dining Room': 'Household Goods',
    'Dolls': 'Toys',
    'Dressers': 'Furniture',
    'Earrings': 'Jewelry',
    'Electronics Other': 'Electronics',
    'Farm Equipment Other': 'Farm Equipment',
    'Figures & Figurines': 'Collectibles',
    'Film': 'Cameras & Photo Equipment',
    'Fine Jewelry': 'Jewelry',
    'Firearms': 'Sports & Hobbies',
    'Fishing': 'Sports & Hobbies',
    'Fitness': 'Sports & Hobbies',
    'Floor Coverings': 'Household Goods',
    'Folk Art': 'Art',
    'Furniture': 'Antiques',
    'Furniture Other': 'Furniture',
    'Games': 'Toys',
    'Games & Toys': 'Collectibles',
    'Gemstones': 'Jewelry',
    'Glassware': 'Household Goods',
    'Golf': 'Sports & Hobbies',
    'Grilling & BBQ': 'Household Goods',
    'Gun Parts': 'Sports & Hobbies',
    'Hand Crafted': 'Art',
    'Hand Tools': 'Tools & Home Improvement',
    'Handbags & Wallets': 'Clothing & Accessories',
    'Hardback': 'Books & Periodicals',
    'Heavy Equipment': 'Farm Equipment',
    'Historical': 'Collectibles',
    'Holiday & Seasonal': 'Collectibles',
    'Home Audio': 'Electronics',
    'Home Video': 'Electronics',
    'Household Goods Other': 'Household Goods',
    'Hunting': 'Sports & Hobbies',
    'Instruments': 'Music',
    'Jewelry Other': 'Jewelry',
    'Keyboards & Pianos': 'Music',
    'Kitchen & Home': 'Household Goods',
    'Knives, Swords, Blades': 'Collectibles',
    'Ladders': 'Tools & Home Improvement',
    'Lawn & Landscape': 'Household Goods',
    'Lighting': 'Household Goods',
    'Linens': 'Household Goods',
    'Luggage': 'Household Goods',
    'Luxury & Designer Items': 'Clothing & Accessories',
    'Magazines & Newspapers': 'Books & Periodicals',
    'Medical Equipment': 'Household Goods',
    "Men's": 'Clothing & Accessories',
    "Men's Jewelry": 'Jewelry',
    'Mexican Art': 'Art',
    'Mid-Century': 'Collectibles',
    'Militaria': 'Collectibles',
    'Mirrors': 'Household Goods',
    'Mobile Devices': 'Electronics',
    'Model Cars': 'Collectibles',
    'Motor Vehicles Other': 'Motor Vehicles',
    'Motorcycles': 'Motor Vehicles',
    'Movie Memorabilia': 'Collectibles',
    'Music Other': 'Music',
    'Mystery Lots': 'Mystery Lots',
    'Native American Art': 'Art',
    'Necklaces': 'Jewelry',
    'Night Stands': 'Furniture',
    'Office': 'Household Goods',
    'Paint & Painting Supplies': 'Household Goods',
    'Paintings': 'Art',
    'Paperback': 'Books & Periodicals',
    'Parts & Accessories': 'Motor Vehicles',
    'Parts & Attachments': 'Farm Equipment',
    'Patio & Deck': 'Household Goods',
    'Patio Furniture': 'Furniture',
    'Pet Supplies': 'Household Goods',
    'Photography': 'Art',
    'Power Tools': 'Tools & Home Improvement',
    'Prints': 'Art',
    'Railroad & Transportation': 'Railroad & Transportation',
    'Records & CDs': 'Music',
    'Retro': 'Collectibles',
    'Rings': 'Jewelry',
    'Rocks, Fossils, Minerals': 'Collectibles',
    'Sailing': 'Sports & Hobbies',
    'Sculpture': 'Art',
    'Seasonal & Holiday': 'Household Goods',
    'Shelving': 'Household Goods',
    'Shoes': 'Clothing & Accessories',
    'Silver': 'Antiques',
    'Sofas': 'Furniture',
    'Sporting Goods Other': 'Sports & Hobbies',
    'Sports Memorabilia': 'Collectibles',
    'Stamps': 'Collectibles',
    'Sterling Silver': 'Household Goods',
    'Storage': 'Household Goods',
    'Storage & Organization': 'Furniture',
    'Sunglasses & Glasses': 'Clothing & Accessories',
    'TVs': 'Electronics',
    'Tables': 'Furniture',
    'Tools Other': 'Tools & Home Improvement',
    'Tools Storage': 'Tools & Home Improvement',
    'Toys Other': 'Toys',
    'Tractors': 'Farm Equipment',
    'Trading Cards': 'Collectibles',
    'Trailers': 'Motor Vehicles',
    'Trains': 'Toys',
    'Trucks': 'Motor Vehicles',
    'Video': 'Cameras & Photo Equipment',
    'Video Games': 'Toys',
    'Vintage': 'Collectibles',
    'Vintage & Antique Toys': 'Toys',
    'Vintage Books': 'Books & Periodicals',
    'Watches': 'Jewelry',
    'Watercolor': 'Art',
    "Women's": 'Clothing & Accessories',
}


def ct_category_group(category: str | None) -> str:
    """Parent group for a CT Seller category leaf. Empty when the leaf is unknown."""
    return _CT_CATEGORY_GROUP.get((category or "").strip(), "")
