"""Unit tests for deterministic Brand / OEM classification."""

from __future__ import annotations

from collector.classification import (
    UNKNOWN,
    classify_brand,
    classify_oem,
    classify_product,
)


# ---------------------------------------------------------------------------
# Positive Brand cases
# ---------------------------------------------------------------------------


def test_brand_intel_core_and_core_ultra() -> None:
    brand, reason = classify_brand(title="Gaming Laptop Intel Core i7-13700H")
    assert brand == "Intel"
    assert "title" in reason

    brand, reason = classify_brand(
        title="MSI Stealth",
        processor="Intel Core Ultra 7 255H",
    )
    assert brand == "Intel"
    assert "processor" in reason


def test_brand_amd_ryzen_and_ryzen_ai() -> None:
    assert classify_brand(title="ASUS TUF AMD Ryzen 7 260")[0] == "AMD"
    assert classify_brand(processor="AMD Ryzen AI 7 350")[0] == "AMD"


def test_brand_qualcomm_snapdragon() -> None:
    assert classify_brand(title="Lenovo Yoga Snapdragon X Elite")[0] == "Qualcomm"
    assert classify_brand(processor="Qualcomm Snapdragon X Plus")[0] == "Qualcomm"


def test_brand_apple_m_series() -> None:
    assert classify_brand(title="Apple MacBook Pro M3 Pro")[0] == "Apple"
    assert classify_brand(processor="Apple M2 Max", title="MacBook Pro")[0] == "Apple"
    assert classify_brand(title="MacBook Air M1 8GB")[0] == "Apple"


# ---------------------------------------------------------------------------
# Positive OEM cases
# ---------------------------------------------------------------------------


def test_oem_dell_hp_lenovo_acer_asus_msi_apple() -> None:
    assert classify_oem(title="Dell XPS 15 Intel Core Ultra")[0] == "Dell"
    assert classify_oem(title="HP Victus 15 Gaming Laptop")[0] == "HP"
    assert classify_oem(title="Lenovo Legion Pro 5")[0] == "Lenovo"
    assert classify_oem(title="Acer Predator Helios Neo")[0] == "Acer"
    assert classify_oem(title="ASUS TUF Gaming A16")[0] == "Asus"
    assert classify_oem(title="Asus ROG Zephyrus G14")[0] == "Asus"
    assert classify_oem(title="MSI CROSSHAIR A16 Gaming Laptop")[0] == "MSI"
    assert classify_oem(title="Apple MacBook Pro 14")[0] == "Apple"


def test_oem_manufacturer_field_preferred() -> None:
    oem, reason = classify_oem(
        title="Generic Gaming Notebook",
        manufacturer="Dell",
    )
    assert oem == "Dell"
    assert reason == "matched_in_manufacturer"


# ---------------------------------------------------------------------------
# Independence examples
# ---------------------------------------------------------------------------


def test_brand_and_oem_are_independent() -> None:
    result = classify_product(
        title="ASUS ROG Strix G16 Gaming Laptop AMD Ryzen 9",
        processor="AMD Ryzen 9 8945HS",
    )
    assert result.brand == "AMD"
    assert result.oem == "Asus"

    result = classify_product(
        title="Dell Latitude Intel Core Ultra 7",
        processor="Intel Core Ultra 7 155H",
    )
    assert result.brand == "Intel"
    assert result.oem == "Dell"

    result = classify_product(
        title="MSI Vector AMD Ryzen 7",
        processor="AMD Ryzen 7 8845HS",
    )
    assert result.brand == "AMD"
    assert result.oem == "MSI"

    result = classify_product(
        title="Apple MacBook Pro M3",
        processor="Apple M3",
    )
    assert result.brand == "Apple"
    assert result.oem == "Apple"


# ---------------------------------------------------------------------------
# Negative cases (false positives)
# ---------------------------------------------------------------------------


def test_hdmi_does_not_classify_as_hp() -> None:
    oem, reason = classify_oem(
        title="MSI Stealth 16 AI Intel Core Ultra7 HDMI ports DisplayPort"
    )
    assert oem == "MSI"
    assert oem != "HP"


def test_short_oem_substrings_do_not_false_positive() -> None:
    # No tracked OEM should be inferred from these strings alone.
    assert classify_oem(title="Premium HDMI cable 2.1")[0] == UNKNOWN
    assert classify_oem(title="Thunderobot Storm Gaming Laptop Intel Core i7")[0] == UNKNOWN


def test_amd_in_unrelated_description_is_not_brand() -> None:
    # Bare "AMD"/"Intel" compatibility copy must not set Brand.
    brand, reason = classify_brand(
        title="Universal Laptop Cooling Pad",
        description="Compatible with AMD and Intel gaming laptops",
    )
    assert brand == UNKNOWN
    assert reason == "insufficient_brand_evidence"


def test_gpu_radeon_alone_does_not_force_amd_brand_over_intel_cpu() -> None:
    brand, reason = classify_brand(
        title="Business Notebook with AMD FreeSync display",
        processor="Intel Core i5-1335U",
    )
    assert brand == "Intel"
    assert "processor" in reason


# ---------------------------------------------------------------------------
# Ambiguous cases → UNKNOWN
# ---------------------------------------------------------------------------


def test_missing_processor_and_weak_title_returns_unknown_brand() -> None:
    brand, reason = classify_brand(title="15 Inch Gaming Notebook 16GB RAM")
    assert brand == UNKNOWN
    assert reason == "insufficient_brand_evidence"


def test_missing_manufacturer_and_unknown_oem_title() -> None:
    oem, reason = classify_oem(title="ACEMAGIC 16 Inch Gaming Laptop Ryzen 7")
    assert oem == UNKNOWN
    assert reason == "insufficient_oem_evidence"


def test_conflicting_brand_signals_return_unknown() -> None:
    brand, reason = classify_brand(
        processor="Intel Core i7 / AMD Ryzen 7 selectable configurations"
    )
    assert brand == UNKNOWN
    assert reason.startswith("conflicting_signals_in_processor")


def test_conflicting_oem_signals_return_unknown() -> None:
    oem, reason = classify_oem(
        title="Dell vs HP comparison bundle retail kit"
    )
    assert oem == UNKNOWN
    assert reason.startswith("conflicting_signals_in_title")


def test_unknown_processor_text_returns_unknown_brand() -> None:
    brand, reason = classify_brand(
        title="Custom DIY PC",
        processor="Unknown OEM CPU 3.2GHz",
    )
    assert brand == UNKNOWN
    assert reason == "insufficient_brand_evidence"


def test_component_cpu_gpu_oem_is_unknown() -> None:
    assert classify_oem(title="AMD Ryzen 7 7800X3D Processor", product_type="cpu")[0] == UNKNOWN
    assert classify_oem(title="NVIDIA GeForce RTX 4090", product_type="gpu")[0] == UNKNOWN


def test_empty_inputs_return_unknown() -> None:
    result = classify_product()
    assert result.brand == UNKNOWN
    assert result.oem == UNKNOWN
    assert result.brand_reason == "insufficient_brand_evidence"
    assert result.oem_reason == "insufficient_oem_evidence"


def test_mediatek_is_other_brand_not_unknown() -> None:
    from collector.classification import OTHER

    brand, reason = classify_brand(title="Chromebook MediaTek Kompanio 520")
    assert brand == OTHER
    assert "other_soc" in reason
    unknown, _ = classify_brand(title="15 Inch Notebook 16GB RAM")
    assert unknown == UNKNOWN
    assert brand != unknown
