"""Product type, platform brand, and eligibility classification fixes."""

from __future__ import annotations

from collector.classification import OTHER, UNKNOWN, classify_brand, classify_oem, classify_product
from collector.normalize import (
    build_normalized_product,
    classify_product_type,
    detect_product_type,
)
from collector.retailers.mercadolibre.classification import (
    EXCLUDED,
    VALID,
    classify_mercadolibre_product,
    is_collection_eligible,
)


def _product(**kwargs):
    defaults = dict(
        retailer_code="newegg",
        country_code="US",
        currency="USD",
        retailer_sku="SKU1",
        source_url="https://www.newegg.com/p/SKU1",
    )
    defaults.update(kwargs)
    return build_normalized_product(**defaults)


def test_standalone_ryzen_cpu_is_cpu_amd() -> None:
    title = (
        "AMD Ryzen 7 7700X3D - Ryzen 7000 Series 8-Core 4.0 GHz "
        "Socket AM5 120W Desktop Processor"
    )
    specs = {
        "Processors Type": "Desktop",
        "Sockets Supported": "AM5",
        "Application": "Cooling device not included - Processor Only",
    }
    product = _product(title=title, specs=specs, processor="Desktop", category_raw="cpu")
    assert product.product_type == "cpu"
    assert product.brand == "AMD"
    assert product.oem == UNKNOWN
    assert product.raw_payload["product_type_reason"] == "standalone_cpu_evidence"
    result = classify_mercadolibre_product(title=title, specs=specs, category_raw="cpu")
    assert result.status == VALID
    assert result.product_type == "cpu"
    assert is_collection_eligible(result)


def test_ryzen_desktop_pc_is_desktop_amd() -> None:
    title = "MSI MAG Infinite RS Gaming Desktop PC AMD Ryzen 7 7700 16GB RTX 4060"
    product = _product(title=title, processor="AMD Ryzen 7 7700", category_raw="desktop")
    assert product.product_type == "desktop"
    assert product.brand == "AMD"
    result = classify_mercadolibre_product(title=title, category_raw="desktop")
    assert result.status == VALID
    assert result.product_type == "desktop"


def test_rtx_5080_graphics_card_is_gpu_other() -> None:
    title = "GIGABYTE GeForce RTX 5080 16GB Graphics Card"
    product = _product(title=title, gpu="GeForce RTX 5080", category_raw="gpu")
    assert product.product_type == "gpu"
    assert product.brand == OTHER
    assert product.oem == UNKNOWN
    result = classify_mercadolibre_product(title=title, category_raw="gpu")
    assert result.status == VALID
    assert result.product_type == "gpu"


def test_yeston_nvidia_gpu_without_pdp_is_gpu_other() -> None:
    title = "Yeston GeForce RTX 5060 Ti 16GB"
    code, reason = classify_product_type(title=title, category_raw="gpu")
    assert code == "gpu"
    brand, brand_reason = classify_brand(title=title)
    assert brand == OTHER
    assert "gpu" in brand_reason


def test_radeon_rx_9070_gre_is_gpu_amd() -> None:
    title = "GIGABYTE Radeon RX 9070 GRE 12GB Graphics Card"
    specs = {
        "Chipset Manufacturer": "AMD",
        "GPU Series": "AMD Radeon RX 9000",
    }
    product = _product(
        title=title,
        gpu="Radeon RX 9070 GRE",
        specs=specs,
        category_raw="gpu",
    )
    assert product.product_type == "gpu"
    assert product.brand == "AMD"
    assert product.raw_payload["brand_reason"] in {
        "radeon_gpu_series",
        "gpu_chipset_manufacturer",
    }
    result = classify_product(
        title=title,
        specifications=specs,
        gpu="Radeon RX 9070 GRE",
        product_type="gpu",
    )
    assert result.brand == "AMD"
    assert result.oem == UNKNOWN


def test_radeon_rx_580_chipset_manufacturer_amd() -> None:
    title = "PELADN RX 580 8GB"
    specs = {"Chipset Manufacturer": "AMD", "Stream Processors": "2048"}
    product = _product(title=title, gpu="AMD", processor="2048", specs=specs)
    assert product.product_type == "gpu"
    assert product.brand == "AMD"


def test_gaming_standing_desk_is_excluded_other() -> None:
    title = 'Electric RGB Gaming Standing Desk 55"'
    result = classify_mercadolibre_product(title=title, category_raw="workstation")
    assert result.status == EXCLUDED
    assert result.product_type == "other"
    assert result.gaming is False
    assert result.exclusion_reason == "FURNITURE"
    assert not is_collection_eligible(result)
    assert detect_product_type(title=title, category_raw="workstation") == "other"


def test_pc_tower_stand_is_excluded_accessory() -> None:
    title = "PC Tower Stand with Wheels"
    result = classify_mercadolibre_product(title=title, category_raw="workstation")
    assert result.status == EXCLUDED
    assert result.product_type == "other"
    assert result.exclusion_reason == "ACCESSORY"
    assert detect_product_type(title=title, category_raw="workstation") == "other"


def test_cpu_holder_and_cubicle_excluded() -> None:
    holder = classify_mercadolibre_product(
        title="Adjustable Computer Tower Stand / CPU Holder",
        category_raw="workstation",
    )
    cubicle = classify_mercadolibre_product(
        title="6-Person Modular Cubicle Desk Workstation",
        category_raw="workstation",
    )
    assert holder.status == EXCLUDED
    assert cubicle.status == EXCLUDED
    assert cubicle.exclusion_reason == "FURNITURE"


def test_drawing_tablet_excluded() -> None:
    title = "XPPen Deco 01 V3 drawing tablet"
    result = classify_mercadolibre_product(title=title, category_raw="tablet")
    assert result.status == EXCLUDED
    assert result.product_type == "other"
    assert result.exclusion_reason == "NON_COMPUTING_PRODUCT"
    assert detect_product_type(title=title, category_raw="tablet") == "other"


def test_apple_ipad_without_soc_is_oem_apple_brand_unknown() -> None:
    title = "REFURBISHED Apple iPad 7 (7th Gen) 32GB - Wi-Fi - 10.2\" - Space Gray"
    product = _product(title=title, category_raw="tablet")
    assert product.product_type == "tablet"
    assert product.oem == "Apple"
    assert product.brand == UNKNOWN
    result = classify_mercadolibre_product(title=title, category_raw="tablet")
    assert result.status == VALID
    assert result.product_type == "tablet"


def test_macbook_m4_is_notebook_apple_brand_and_oem() -> None:
    title = "Apple MacBook Pro 14-inch M4 Pro"
    product = _product(title=title, processor="Apple M4 Pro")
    assert product.product_type == "notebook"
    assert product.oem == "Apple"
    assert product.brand == "Apple"
    assert "apple_silicon" in product.raw_payload["brand_reason"] or "processor" in product.raw_payload["brand_reason"]


def test_snapdragon_x_elite_laptop_is_notebook_qualcomm() -> None:
    title = "Lenovo Yoga Slim 7x Snapdragon X Elite Laptop"
    product = _product(title=title, processor="Snapdragon X Elite")
    assert product.product_type == "notebook"
    assert product.brand == "Qualcomm"
    brand, reason = classify_brand(title=title)
    assert brand == "Qualcomm"
    assert reason == "snapdragon_title"


def test_snapdragon_tablet_is_tablet_qualcomm() -> None:
    title = "Samsung Galaxy Tab Snapdragon 8 Gen 3 Android tablet"
    product = _product(title=title)
    assert product.product_type == "tablet"
    assert product.brand == "Qualcomm"


def test_mediatek_helio_tablet_is_other() -> None:
    title = 'HAOVM 10" Android Tablet MediaTek Helio G80'
    product = _product(title=title)
    assert product.product_type == "tablet"
    assert product.brand == OTHER


def test_exynos_tablet_is_other() -> None:
    title = "Samsung Galaxy Tab S6 Lite Android tablet"
    product = _product(title=title, processor="Samsung Exynos 1280")
    assert product.product_type == "tablet"
    assert product.brand == OTHER


def test_unisoc_tablet_is_other() -> None:
    title = 'JIMTAB 11" Android 14 tablet'
    product = _product(title=title, processor="UNISOC T616")
    assert product.product_type == "tablet"
    assert product.brand == OTHER


def test_octa_core_android_tablet_stays_unknown() -> None:
    title = 'JIMTAB 11" Android 14 Octa Core tablet'
    product = _product(title=title, processor="Octa Core")
    assert product.product_type == "tablet"
    assert product.brand == UNKNOWN


def test_android_tablet_no_soc_stays_unknown() -> None:
    title = 'Aheadthink 7" Kids Tablet Android 12'
    product = _product(title=title)
    assert product.product_type == "tablet"
    assert product.brand == UNKNOWN

    headwolf = _product(title='Headwolf Wpad7 Android 15 11" 8-core')
    assert headwolf.product_type == "tablet"
    assert headwolf.brand == UNKNOWN


def test_sc98963a_chipset_stays_unknown() -> None:
    title = 'JIMTAB 10" Android 13 2-in-1 tablet'
    product = _product(title=title, gpu="SC98963A", processor="8 cores 8 threads")
    assert product.product_type == "tablet"
    assert product.brand == UNKNOWN


def test_processors_type_desktop_does_not_make_cpu_a_desktop() -> None:
    title = "AMD Ryzen 7 5800X3D Socket AM4 Desktop Processor"
    assert (
        detect_product_type(
            title=title,
            specs={"Processors Type": "Desktop"},
            category_raw="cpu",
        )
        == "cpu"
    )


def test_gpu_oem_remains_unknown_for_untracked_board_partner() -> None:
    assert classify_oem(title="GIGABYTE Radeon RX 9070 GRE", product_type="gpu")[0] == UNKNOWN
    assert classify_oem(title="NVIDIA GeForce RTX 4090", product_type="gpu")[0] == UNKNOWN
