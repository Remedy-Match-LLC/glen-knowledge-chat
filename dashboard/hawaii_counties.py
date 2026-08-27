"""FMP-compatible ZIP-to-QBO-class categorization for sales transactions."""

import re


# Copied from FMP's tax_zip_codes table (2026-05-23 full extract).  Sets keep
# duplicate city rows for the same ZIP from changing the lookup result.
_COUNTY_ZIPS = {
    "Hawaii County": set("""
        96704 96710 96718 96719 96720 96721 96725 96726 96727 96728 96737
        96738 96739 96740 96743 96745 96749 96750 96755 96760 96764 96771
        96772 96773 96774 96776 96777 96778 96780 96781 96783 96785
    """.split()),
    "Honolulu County": set("""
        96701 96706 96707 96709 96712 96717 96730 96731 96734 96744 96759
        96762 96782 96786 96789 96791 96792 96795 96797 96816
    """.split()),
    "Kauai County": set("""
        96703 96705 96714 96715 96716 96722 96741 96746 96747 96751 96752
        96754 96756 96765 96766 96769 96796
    """.split()),
    "Maui County": set("""
        96708 96713 96729 96732 96733 96742 96748 96753 96757 96761 96763
        96767 96768 96770 96779 96784 96788 96790 96793
    """.split()),
}

_ZIP_TO_CATEGORY = {
    zip_code: county
    for county, zip_codes in _COUNTY_ZIPS.items()
    for zip_code in zip_codes
}


def category_for_zip(postal_code):
    """Return an FMP county, Non-Hawaii, or None when no usable ZIP exists.

    ZIP+4 values use their first five digits. A nonempty postal code outside
    FMP's Hawaii ZIP table (including international formats) is Non-Hawaii;
    a missing postal code remains uncategorized rather than being guessed.
    """
    value = str(postal_code or "").strip()
    if not value:
        return None
    match = re.match(r"^(\d{5})(?:-\d{4})?$", value)
    return _ZIP_TO_CATEGORY.get(match.group(1), "Non-Hawaii") if match else "Non-Hawaii"
