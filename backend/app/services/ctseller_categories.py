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

