# -*- coding: utf-8 -*-
"""
Image Sorter - Monolithic Edition
=====================================

برنامج واحد كامل لفرز الصور المسترجعة حسب الجودة/الأبعاد

"""

import os
import sys
import csv
import json
import time
import shutil
import threading
import subprocess
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image, ImageFile, UnidentifiedImageError
    PIL_AVAILABLE = True
except Exception:
    Image = None
    ImageFile = None
    UnidentifiedImageError = Exception
    PIL_AVAILABLE = False

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText


APP_NAME = "Image Sorter Pro"
APP_VERSION = "1.1.0"
SCRIPT_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = SCRIPT_DIR / "image_sorter_settings.json"

DEFAULT_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp",
    ".tif", ".tiff", ".ico"
)

OUTPUT_FOLDER_NAMES = {
    "sorted",
    "low",
    "high",
    "very_low",
    "medium",
    "ultra",
    "corrupted",
    "non_images",
    "reports",
}


@dataclass
class SortSettings:
    base_path: str = r"E:\Private\#Private#\restoredpics"
    output_folder_name: str = "sorted"
    operation_mode: str = "copy"  # copy | move
    scan_mode: str = "numbered"   # numbered | recursive | current_only
    numbered_prefix: str = "folder_"
    start_folder: int = 1
    end_folder: int = 108

    classification_mode: str = "multi_quality"
    # classification_mode:
    # multi_quality
    # simple_and_640
    # strict_or_640

    min_width: int = 640
    min_height: int = 640

    very_low_max_pixels: int = 90_000       # أقل من 300x300 تقريبًا
    low_max_pixels: int = 410_000           # أقل من 640x640 تقريبًا
    medium_max_pixels: int = 1_000_000      # تقريبًا HD خفيف
    high_max_pixels: int = 3_000_000        # أعلى من ذلك ultra

    use_min_side_downgrade: bool = True
    low_min_side: int = 640
    very_low_min_side: int = 300

    prefix_source_folder: bool = True
    keep_relative_structure: bool = False
    process_non_images: bool = False
    strict_image_validation: bool = True
    allow_truncated_images: bool = False
    lowercase_extensions: bool = True
    write_report: bool = True
    skip_existing_exact: bool = False
    include_subfolders_in_numbered_mode: bool = False

    extensions_csv: str = ",".join(DEFAULT_EXTENSIONS)


@dataclass
class FileReportRow:
    status: str
    category: str
    operation: str
    original_path: str
    destination_path: str
    filename: str
    width: str
    height: str
    pixels: str
    file_size_kb: str
    source_folder: str
    error: str


class SortEngine:
    def __init__(self, settings: SortSettings, log_callback=None, progress_callback=None, finish_callback=None, stop_event=None):
        self.settings = settings
        self.log_callback = log_callback
        self.progress_callback = progress_callback
        self.finish_callback = finish_callback
        self.stop_event = stop_event or threading.Event()

        self.stats: Dict[str, int] = {
            "total_candidates": 0,
            "images_done": 0,
            "copied": 0,
            "moved": 0,
            "ignored_non_images": 0,
            "processed_non_images": 0,
            "corrupted": 0,
            "errors": 0,
            "missing_folders": 0,
            "skipped_existing": 0,
            "very_low": 0,
            "low": 0,
            "medium": 0,
            "high": 0,
            "ultra": 0,
        }

        self.report_rows: List[FileReportRow] = []
        self.output_root: Optional[Path] = None
        self.reports_folder: Optional[Path] = None

        if ImageFile is not None:
            ImageFile.LOAD_TRUNCATED_IMAGES = bool(settings.allow_truncated_images)

    def log(self, message: str):
        if self.log_callback:
            self.log_callback(message)
        else:
            print(message)

    def progress(self, current: int, total: int, message: str = ""):
        if self.progress_callback:
            self.progress_callback(current, total, message)

    def finish(self, success: bool, message: str):
        if self.finish_callback:
            self.finish_callback(success, message)

    def run(self):
        started_at = time.time()

        try:
            if not PIL_AVAILABLE:
                raise RuntimeError("مكتبة Pillow غير مثبتة. نفّذ الأمر: pip install pillow")

            self.validate_settings()
            self.prepare_output_folders()

            self.log("=" * 80)
            self.log(f"{APP_NAME} v{APP_VERSION}")
            self.log(f"وقت البدء: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.log(f"المسار الأساسي: {self.settings.base_path}")
            self.log(f"وضع العملية: {self.settings.operation_mode.upper()}")
            self.log(f"طريقة الفحص: {self.settings.scan_mode}")
            self.log(f"طريقة التصنيف: {self.settings.classification_mode}")
            self.log("=" * 80)

            files = self.collect_files()
            total = len(files)
            self.stats["total_candidates"] = total

            if total == 0:
                self.log("لم يتم العثور على أي ملفات مرشحة للمعالجة.")
                self.finish(True, "انتهى التشغيل بدون ملفات للمعالجة.")
                return

            self.log(f"عدد الملفات المرشحة للمعالجة: {total}")

            for index, file_path in enumerate(files, start=1):
                if self.stop_event.is_set():
                    self.log("تم إيقاف العملية بواسطة المستخدم.")
                    break

                self.progress(index, total, f"معالجة: {file_path.name}")
                self.process_file(file_path)

            if self.settings.write_report:
                self.write_csv_report()

            elapsed = time.time() - started_at
            self.log_summary(elapsed)

            if self.stop_event.is_set():
                self.finish(False, "تم إيقاف العملية قبل الانتهاء.")
            else:
                self.finish(True, "تم الانتهاء من فرز الصور بنجاح.")

        except Exception as exc:
            self.stats["errors"] += 1
            self.log(f"خطأ عام: {exc}")
            self.finish(False, f"حدث خطأ: {exc}")

    def validate_settings(self):
        base = Path(self.settings.base_path)

        if not base.exists():
            raise ValueError(f"المسار الأساسي غير موجود: {base}")

        if not base.is_dir():
            raise ValueError(f"المسار الأساسي ليس فولدر: {base}")

        if self.settings.operation_mode not in {"copy", "move"}:
            raise ValueError("operation_mode يجب أن يكون copy أو move")

        if self.settings.scan_mode not in {"numbered", "recursive", "current_only"}:
            raise ValueError("scan_mode يجب أن يكون numbered أو recursive أو current_only")

        if self.settings.classification_mode not in {"multi_quality", "simple_and_640", "strict_or_640"}:
            raise ValueError("classification_mode غير صحيح")

        if self.settings.start_folder > self.settings.end_folder:
            raise ValueError("رقم بداية الفولدر لا يمكن أن يكون أكبر من رقم النهاية")

        numeric_fields = [
            "min_width", "min_height", "very_low_max_pixels",
            "low_max_pixels", "medium_max_pixels", "high_max_pixels",
            "low_min_side", "very_low_min_side",
        ]

        for field_name in numeric_fields:
            value = getattr(self.settings, field_name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"القيمة {field_name} يجب أن تكون رقمًا صحيحًا موجبًا")

        if not self.parse_extensions():
            raise ValueError("يجب تحديد امتداد صورة واحد على الأقل")

    def prepare_output_folders(self):
        base = Path(self.settings.base_path)
        self.output_root = base / self.settings.output_folder_name
        self.reports_folder = self.output_root / "reports"

        categories = self.get_active_categories()
        for category in categories:
            (self.output_root / category).mkdir(parents=True, exist_ok=True)

        if self.settings.process_non_images:
            (self.output_root / "non_images").mkdir(parents=True, exist_ok=True)

        if self.settings.write_report:
            self.reports_folder.mkdir(parents=True, exist_ok=True)

    def get_active_categories(self) -> List[str]:
        if self.settings.classification_mode in {"simple_and_640", "strict_or_640"}:
            return ["low", "high", "corrupted"]

        return ["very_low", "low", "medium", "high", "ultra", "corrupted"]

    def parse_extensions(self) -> Tuple[str, ...]:
        raw = self.settings.extensions_csv or ""
        parts = []

        for item in raw.split(","):
            ext = item.strip()
            if not ext:
                continue

            if not ext.startswith("."):
                ext = "." + ext

            if self.settings.lowercase_extensions:
                ext = ext.lower()

            parts.append(ext)

        return tuple(sorted(set(parts)))

    def is_image_by_extension(self, file_path: Path) -> bool:
        suffix = file_path.suffix or ""
        if self.settings.lowercase_extensions:
            suffix = suffix.lower()
        return suffix in self.parse_extensions()

    def is_inside_output(self, path: Path) -> bool:
        if not self.output_root:
            return False

        try:
            path.resolve().relative_to(self.output_root.resolve())
            return True
        except ValueError:
            return False

    def collect_files(self) -> List[Path]:
        base = Path(self.settings.base_path)
        files: List[Path] = []

        if self.settings.scan_mode == "current_only":
            for item in base.iterdir():
                if item.is_file() and not self.is_inside_output(item):
                    files.append(item)
            return files

        if self.settings.scan_mode == "numbered":
            for folder_index in range(self.settings.start_folder, self.settings.end_folder + 1):
                folder = base / f"{self.settings.numbered_prefix}{folder_index}"

                if not folder.exists():
                    self.stats["missing_folders"] += 1
                    self.log(f"Folder not found: {folder}")
                    continue

                if not folder.is_dir():
                    self.log(f"تم التجاهل لأنه ليس فولدر: {folder}")
                    continue

                if self.is_inside_output(folder):
                    continue

                if self.settings.include_subfolders_in_numbered_mode:
                    files.extend(self.collect_files_recursive(folder))
                else:
                    for item in folder.iterdir():
                        if item.is_file() and not self.is_inside_output(item):
                            files.append(item)

            return files

        if self.settings.scan_mode == "recursive":
            return self.collect_files_recursive(base)

        return files

    def collect_files_recursive(self, root: Path) -> List[Path]:
        files: List[Path] = []
        output_root_resolved = self.output_root.resolve() if self.output_root else None

        for current_root, dirs, filenames in os.walk(root):
            current_path = Path(current_root)

            if output_root_resolved:
                try:
                    current_path.resolve().relative_to(output_root_resolved)
                    dirs[:] = []
                    continue
                except ValueError:
                    pass

            # منع الدخول في فولدرات نتائج معروفة لو كانت موجودة داخل المسار
            dirs[:] = [
                d for d in dirs
                if d not in OUTPUT_FOLDER_NAMES and not d.startswith(".")
            ]

            for filename in filenames:
                file_path = current_path / filename
                if not self.is_inside_output(file_path):
                    files.append(file_path)

        return files

    def process_file(self, file_path: Path):
        source_folder = file_path.parent.name

        if not self.is_image_by_extension(file_path):
            if self.settings.process_non_images:
                self.process_non_image(file_path)
            else:
                self.stats["ignored_non_images"] += 1
                self.add_report(
                    status="ignored_non_image",
                    category="",
                    operation="none",
                    original_path=file_path,
                    destination_path="",
                    filename=file_path.name,
                    width="",
                    height="",
                    pixels="",
                    file_size_kb=self.safe_file_size_kb(file_path),
                    source_folder=source_folder,
                    error="File extension is not configured as image",
                )
            return

        try:
            width, height = self.read_image_dimensions(file_path)
            pixels = width * height
            category = self.classify_image(width, height)
            destination_path = self.build_destination_path(file_path, category)

            if self.settings.skip_existing_exact and destination_path.exists():
                self.stats["skipped_existing"] += 1
                self.add_report(
                    status="skipped_existing",
                    category=category,
                    operation="none",
                    original_path=file_path,
                    destination_path=destination_path,
                    filename=file_path.name,
                    width=str(width),
                    height=str(height),
                    pixels=str(pixels),
                    file_size_kb=self.safe_file_size_kb(file_path),
                    source_folder=source_folder,
                    error="Destination file already exists",
                )
                self.log(f"Skipped existing: {destination_path}")
                return

            actual_destination = self.copy_or_move(file_path, destination_path)

            self.stats["images_done"] += 1
            self.stats[category] = self.stats.get(category, 0) + 1

            if self.settings.operation_mode == "copy":
                self.stats["copied"] += 1
            else:
                self.stats["moved"] += 1

            self.add_report(
                status="success",
                category=category,
                operation=self.settings.operation_mode,
                original_path=file_path,
                destination_path=actual_destination,
                filename=file_path.name,
                width=str(width),
                height=str(height),
                pixels=str(pixels),
                file_size_kb=self.safe_file_size_kb(actual_destination),
                source_folder=source_folder,
                error="",
            )

            self.log(f"{self.settings.operation_mode.upper()}: {file_path} -> {actual_destination}")

        except Exception as exc:
            self.process_corrupted_or_error(file_path, exc)

    def process_non_image(self, file_path: Path):
        try:
            destination_path = self.build_destination_path(file_path, "non_images")
            actual_destination = self.copy_or_move(file_path, destination_path)

            self.stats["processed_non_images"] += 1

            self.add_report(
                status="processed_non_image",
                category="non_images",
                operation=self.settings.operation_mode,
                original_path=file_path,
                destination_path=actual_destination,
                filename=file_path.name,
                width="",
                height="",
                pixels="",
                file_size_kb=self.safe_file_size_kb(actual_destination),
                source_folder=file_path.parent.name,
                error="",
            )

            self.log(f"{self.settings.operation_mode.upper()} NON-IMAGE: {file_path} -> {actual_destination}")

        except Exception as exc:
            self.stats["errors"] += 1
            self.add_report(
                status="error_non_image",
                category="non_images",
                operation=self.settings.operation_mode,
                original_path=file_path,
                destination_path="",
                filename=file_path.name,
                width="",
                height="",
                pixels="",
                file_size_kb=self.safe_file_size_kb(file_path),
                source_folder=file_path.parent.name,
                error=str(exc),
            )
            self.log(f"Error processing non-image {file_path}: {exc}")

    def read_image_dimensions(self, file_path: Path) -> Tuple[int, int]:
        if Image is None:
            raise RuntimeError("Pillow is not available")

        if self.settings.strict_image_validation:
            try:
                with Image.open(file_path) as img:
                    width, height = img.size
                    img.verify()
                return width, height
            except UnidentifiedImageError as exc:
                raise ValueError(f"Cannot identify image file: {exc}")
            except Exception as exc:
                raise ValueError(f"Image validation failed: {exc}")

        try:
            with Image.open(file_path) as img:
                return img.size
        except UnidentifiedImageError as exc:
            raise ValueError(f"Cannot identify image file: {exc}")

    def classify_image(self, width: int, height: int) -> str:
        mode = self.settings.classification_mode

        if mode == "simple_and_640":
            if width < self.settings.min_width and height < self.settings.min_height:
                return "low"
            return "high"

        if mode == "strict_or_640":
            if width < self.settings.min_width or height < self.settings.min_height:
                return "low"
            return "high"

        pixels = width * height
        min_side = min(width, height)

        if self.settings.use_min_side_downgrade:
            if min_side < self.settings.very_low_min_side:
                return "very_low"
            if min_side < self.settings.low_min_side:
                if pixels < self.settings.medium_max_pixels:
                    return "low"

        if pixels < self.settings.very_low_max_pixels:
            return "very_low"

        if pixels < self.settings.low_max_pixels:
            return "low"

        if pixels < self.settings.medium_max_pixels:
            return "medium"

        if pixels < self.settings.high_max_pixels:
            return "high"

        return "ultra"

    def build_destination_path(self, file_path: Path, category: str) -> Path:
        if not self.output_root:
            raise RuntimeError("Output root is not prepared")

        base = Path(self.settings.base_path)
        category_folder = self.output_root / category
        category_folder.mkdir(parents=True, exist_ok=True)

        original_name = file_path.name

        if self.settings.prefix_source_folder:
            safe_source = self.safe_name(file_path.parent.name)
            original_name = f"{safe_source}__{original_name}"

        if self.settings.keep_relative_structure:
            try:
                relative_parent = file_path.parent.relative_to(base)
                destination_folder = category_folder / relative_parent
                destination_folder.mkdir(parents=True, exist_ok=True)
                return self.unique_path(destination_folder / original_name)
            except ValueError:
                pass

        return self.unique_path(category_folder / original_name)

    def unique_path(self, destination: Path) -> Path:
        if not destination.exists():
            return destination

        stem = destination.stem
        suffix = destination.suffix
        parent = destination.parent

        counter = 1
        while True:
            candidate = parent / f"{stem}_{counter:03d}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def copy_or_move(self, source: Path, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)

        if self.settings.operation_mode == "copy":
            shutil.copy2(str(source), str(destination))
        else:
            shutil.move(str(source), str(destination))

        return destination

    def process_corrupted_or_error(self, file_path: Path, exc: Exception):
        self.stats["corrupted"] += 1
        error_text = str(exc)

        try:
            destination_path = self.build_destination_path(file_path, "corrupted")
            actual_destination = self.copy_or_move(file_path, destination_path)
            operation = self.settings.operation_mode

            if self.settings.operation_mode == "copy":
                self.stats["copied"] += 1
            else:
                self.stats["moved"] += 1

            self.add_report(
                status="corrupted",
                category="corrupted",
                operation=operation,
                original_path=file_path,
                destination_path=actual_destination,
                filename=file_path.name,
                width="",
                height="",
                pixels="",
                file_size_kb=self.safe_file_size_kb(actual_destination),
                source_folder=file_path.parent.name,
                error=error_text,
            )

            self.log(f"CORRUPTED: {file_path} -> {actual_destination} | {error_text}")

        except Exception as move_exc:
            self.stats["errors"] += 1
            self.add_report(
                status="error",
                category="corrupted",
                operation="none",
                original_path=file_path,
                destination_path="",
                filename=file_path.name,
                width="",
                height="",
                pixels="",
                file_size_kb=self.safe_file_size_kb(file_path),
                source_folder=file_path.parent.name,
                error=f"{error_text} | Failed to send to corrupted: {move_exc}",
            )
            self.log(f"ERROR: {file_path} | {error_text} | Failed to send to corrupted: {move_exc}")

    def add_report(
        self,
        status,
        category,
        operation,
        original_path,
        destination_path,
        filename,
        width,
        height,
        pixels,
        file_size_kb,
        source_folder,
        error,
    ):
        row = FileReportRow(
            status=str(status),
            category=str(category),
            operation=str(operation),
            original_path=str(original_path),
            destination_path=str(destination_path),
            filename=str(filename),
            width=str(width),
            height=str(height),
            pixels=str(pixels),
            file_size_kb=str(file_size_kb),
            source_folder=str(source_folder),
            error=str(error),
        )
        self.report_rows.append(row)

    def safe_file_size_kb(self, file_path) -> str:
        try:
            p = Path(file_path)
            if p.exists():
                return f"{p.stat().st_size / 1024:.2f}"
        except Exception:
            pass
        return ""

    def safe_name(self, value: str) -> str:
        bad_chars = '<>:"/\\|?*'
        cleaned = "".join("_" if ch in bad_chars else ch for ch in value)
        cleaned = cleaned.strip().strip(".")
        return cleaned or "unknown"

    def write_csv_report(self):
        if not self.reports_folder:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = self.reports_folder / f"sorting_report_{timestamp}.csv"

        fieldnames = [
            "status",
            "category",
            "operation",
            "original_path",
            "destination_path",
            "filename",
            "width",
            "height",
            "pixels",
            "file_size_kb",
            "source_folder",
            "error",
        ]

        with open(report_path, "w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()

            for row in self.report_rows:
                writer.writerow(asdict(row))

        self.log(f"تم إنشاء التقرير: {report_path}")

    def log_summary(self, elapsed: float):
        self.log("")
        self.log("=" * 80)
        self.log("ملخص التشغيل")
        self.log("=" * 80)
        self.log(f"إجمالي الملفات المرشحة: {self.stats['total_candidates']}")
        self.log(f"صور تمت معالجتها بنجاح: {self.stats['images_done']}")
        self.log(f"تم النسخ: {self.stats['copied']}")
        self.log(f"تم النقل: {self.stats['moved']}")
        self.log(f"صور تالفة/غير قابلة للفتح: {self.stats['corrupted']}")
        self.log(f"ملفات غير صور تم تجاهلها: {self.stats['ignored_non_images']}")
        self.log(f"ملفات غير صور تم التعامل معها: {self.stats['processed_non_images']}")
        self.log(f"فولدرات غير موجودة: {self.stats['missing_folders']}")
        self.log(f"ملفات متخطاة لأنها موجودة: {self.stats['skipped_existing']}")
        self.log(f"أخطاء: {self.stats['errors']}")

        for category in ["very_low", "low", "medium", "high", "ultra"]:
            if self.stats.get(category, 0):
                self.log(f"{category}: {self.stats[category]}")

        self.log(f"الوقت المستغرق: {elapsed:.2f} ثانية")
        self.log("=" * 80)


class ImageSorterApp:
    """
    واجهة احترافية مقسّمة إلى تبويبات.
    كل تبويب يحتوي على Scroll مستقل، وكل إعداد بجانبه زر شرح ؟ يوضح تأثيره.
    """

    HELP_TEXTS = {
        "base_path": "المسار الأساسي الذي يحتوي على الصور أو الفولدرات المراد فحصها. النتائج ستُنشأ داخله داخل فولدر النتائج.",
        "output_folder_name": "اسم فولدر النتائج الذي سيتم إنشاؤه داخل المسار الأساسي. الافتراضي sorted لتجنب خلط النتائج بالصور الأصلية.",
        "scan_numbered": "يفحص فولدرات مرقمة مثل folder_1 إلى folder_108. مناسب للصور المسترجعة من برامج Recovery التي تقسم الملفات إلى فولدرات مرقمة.",
        "scan_recursive": "يفحص كل الفولدرات الداخلية تحت المسار الأساسي. البرنامج يتجنب فولدر النتائج تلقائيًا حتى لا يعيد معالجة الصور التي تم فرزها.",
        "scan_current_only": "يفحص الملفات الموجودة مباشرة داخل المسار الأساسي فقط، ولا يدخل إلى أي فولدرات فرعية.",
        "numbered_prefix": "بادئة أسماء الفولدرات الرقمية. لو فولدراتك اسمها folder_1 اتركها folder_. لو اسمها dir_1 اجعلها dir_.",
        "start_folder": "أول رقم فولدر سيتم فحصه في وضع الفولدرات الرقمية.",
        "end_folder": "آخر رقم فولدر سيتم فحصه في وضع الفولدرات الرقمية.",
        "include_subfolders_in_numbered_mode": "عند تفعيله، إذا كان داخل folder_1 فولدرات أخرى، سيتم فحصها أيضًا. اتركه مغلقًا لو تريد فحص الملفات المباشرة فقط.",
        "operation_copy": "ينسخ الملفات إلى النتائج ويترك الأصل كما هو. هذا هو الوضع الآمن والمفضل في البداية.",
        "operation_move": "ينقل الملفات من أماكنها الأصلية إلى النتائج. استخدمه فقط بعد التأكد من الإعدادات لأن أماكن الملفات الأصلية ستتغير.",
        "prefix_source_folder": "يضيف اسم الفولدر الأصلي قبل اسم الصورة مثل folder_15__image.jpg. هذا يقلل جدًا مشكلة تكرار أسماء الصور.",
        "keep_relative_structure": "يحافظ على مسار الفولدرات النسبي داخل كل تصنيف. مفيد لو تريد معرفة الهيكل القديم للصور بعد الفرز.",
        "skip_existing_exact": "إذا كان الملف الناتج موجودًا بنفس الاسم، سيتم تخطيه بدل إنشاء اسم جديد. في أغلب الحالات اتركه مغلقًا لأن البرنامج ينشئ اسمًا فريدًا تلقائيًا.",
        "process_non_images": "ينسخ/ينقل الملفات غير الصور إلى فولدر non_images بدل تجاهلها. مفيد لو تريد حفظ كل ما تم استرجاعه وليس الصور فقط.",
        "write_report": "ينشئ تقرير CSV يحتوي على كل ملف: المسار الأصلي، المسار الجديد، الأبعاد، التصنيف، الحالة، وأي خطأ.",
        "classification_multi": "تصنيف احترافي متعدد حسب عدد البكسلات وأقل ضلع: very_low / low / medium / high / ultra. هذا هو الخيار الأفضل للتنظيف الشامل.",
        "classification_and": "نفس منطق الكود الأصلي: الصورة low فقط إذا كان العرض أقل من الحد والارتفاع أقل من الحد معًا.",
        "classification_or": "منطق أكثر صرامة: الصورة low إذا كان العرض أقل من الحد أو الارتفاع أقل من الحد. يفصل الصور الضيقة أو الصغيرة بسرعة.",
        "min_width": "الحد الأدنى للعرض المستخدم في وضع AND أو OR. الافتراضي 640.",
        "min_height": "الحد الأدنى للارتفاع المستخدم في وضع AND أو OR. الافتراضي 640.",
        "very_low_max_pixels": "في التصنيف المتعدد: أي صورة عدد بكسلاتها أقل من هذه القيمة تُصنف very_low، إلا إذا أثر شرط أقل ضلع.",
        "low_max_pixels": "في التصنيف المتعدد: الصور الأقل من هذه القيمة تُصنف low بعد استبعاد very_low.",
        "medium_max_pixels": "في التصنيف المتعدد: الصور الأقل من هذه القيمة تُصنف medium بعد استبعاد low.",
        "high_max_pixels": "في التصنيف المتعدد: الصور الأقل من هذه القيمة تُصنف high، وما يزيد عنها يُصنف ultra.",
        "use_min_side_downgrade": "لو الصورة طويلة جدًا لكن ضيقة جدًا، قد تبدو كبيرة بالبكسلات لكنها ضعيفة عمليًا. هذا الخيار يخفض تصنيفها حسب أقل ضلع.",
        "very_low_min_side": "إذا كان أقل ضلع في الصورة أقل من هذه القيمة، يتم اعتبارها very_low عند تفعيل خيار أقل ضلع.",
        "low_min_side": "إذا كان أقل ضلع في الصورة أقل من هذه القيمة، يتم اعتبارها low عند تفعيل خيار أقل ضلع، خصوصًا للصور الضيقة.",
        "strict_image_validation": "يفتح الصورة ثم ينفذ verify لاكتشاف الصور التالفة. أدق لكنه قد يكون أبطأ قليلًا.",
        "allow_truncated_images": "يحاول قراءة الصور غير المكتملة Truncated. مفيد للصور المسترجعة، لكنه قد يسمح بمرور صور بها تلف جزئي.",
        "lowercase_extensions": "يجعل مقارنة الامتدادات غير حساسة لحالة الحروف، مثل JPG و jpg.",
        "extensions_csv": "قائمة الامتدادات المسموحة مفصولة بفواصل. يمكنك إضافة webp أو heic إذا كانت مدعومة في بيئتك.",
        "save_settings": "يحفظ الإعدادات الحالية في ملف JSON بجانب البرنامج ليتم تحميلها تلقائيًا لاحقًا.",
        "load_settings": "يعيد تحميل الإعدادات من ملف JSON المحفوظ.",
        "reset_defaults": "يرجع كل القيم إلى الوضع الافتراضي الآمن.",
        "open_output_folder": "يفتح فولدر النتائج الحالي إذا كان موجودًا.",
    }

    def __init__(self, root):
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("1250x820")
        self.root.minsize(980, 680)

        self.settings = load_settings()
        self.worker_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.output_root_last: Optional[Path] = None
        self.vars = {}
        self.scroll_canvases = []

        self.build_ui()
        self.apply_settings_to_ui()
        self.update_scan_mode_state()
        self.update_classification_state()
        self.log(f"تم تشغيل {APP_NAME}. الوضع الافتراضي الآمن هو COPY.")

        if not PIL_AVAILABLE:
            self.log("تحذير: مكتبة Pillow غير مثبتة. لن يعمل الفرز قبل تثبيتها.")
            messagebox.showwarning(
                "Pillow غير مثبتة",
                "مكتبة Pillow غير مثبتة.\n\nنفّذ الأمر التالي في CMD:\n\npip install pillow"
            )

    # =========================
    # UI BUILDING
    # =========================

    def build_ui(self):
        self.configure_style()

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        self.build_header(main)

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True)

        self.paths_tab = self.create_scrollable_tab("1) المسارات")
        self.scan_tab = self.create_scrollable_tab("2) الفحص")
        self.operation_tab = self.create_scrollable_tab("3) العملية والحماية")
        self.classification_tab = self.create_scrollable_tab("4) التصنيف")
        self.advanced_tab = self.create_scrollable_tab("5) المتقدم")
        self.log_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(self.log_tab, text="6) التشغيل والسجل")

        self.create_paths_section(self.paths_tab)
        self.create_scan_section(self.scan_tab)
        self.create_operation_section(self.operation_tab)
        self.create_classification_section(self.classification_tab)
        self.create_advanced_section(self.advanced_tab)
        self.create_settings_buttons(self.advanced_tab)
        self.build_log_panel(self.log_tab)
        self.build_bottom_bar(main)

    def build_header(self, parent):
        header = ttk.Frame(parent)
        header.pack(fill="x", pady=(0, 10))

        title = ttk.Label(header, text="Image Sorter Pro", style="Title.TLabel")
        title.pack(side="left")

        subtitle = ttk.Label(
            header,
            text="واجهة Tabs + Scroll + شرح لكل إعداد — مناسب لفرز الصور المسترجعة بأمان",
            style="Subtitle.TLabel",
        )
        subtitle.pack(side="left", padx=18)

        ttk.Button(header, text="شرح عام", command=self.show_general_help).pack(side="right")

    def configure_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10))
        style.configure("Section.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Success.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Help.TButton", font=("Segoe UI", 9, "bold"), padding=(4, 2))
        style.configure("TButton", padding=6)
        style.configure("TLabel", font=("Segoe UI", 10))
        style.configure("TCheckbutton", font=("Segoe UI", 10))
        style.configure("TRadiobutton", font=("Segoe UI", 10))
        style.configure("TNotebook.Tab", padding=(14, 7), font=("Segoe UI", 10, "bold"))

    def create_scrollable_tab(self, tab_title: str):
        outer = ttk.Frame(self.notebook)
        self.notebook.add(outer, text=tab_title)

        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, padding=10)

        window_id = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        def on_content_configure(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def on_canvas_configure(event):
            canvas.itemconfigure(window_id, width=event.width)

        content.bind("<Configure>", on_content_configure)
        canvas.bind("<Configure>", on_canvas_configure)

        def _on_mousewheel(event):
            # Windows/macOS/Linux compatibility as much as Tkinter allows.
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(1, "units")
            else:
                delta = int(-1 * (event.delta / 120)) if event.delta else 0
                canvas.yview_scroll(delta, "units")

        def bind_mousewheel(_event=None):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_mousewheel)
            canvas.bind_all("<Button-5>", _on_mousewheel)

        def unbind_mousewheel(_event=None):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        content.bind("<Enter>", bind_mousewheel)
        content.bind("<Leave>", unbind_mousewheel)
        canvas.bind("<Enter>", bind_mousewheel)
        canvas.bind("<Leave>", unbind_mousewheel)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.scroll_canvases.append(canvas)
        return content

    def help_button(self, parent, key: str):
        return ttk.Button(
            parent,
            text="؟",
            width=3,
            style="Help.TButton",
            command=lambda: self.show_help(key),
        )

    def show_help(self, key: str):
        text = self.HELP_TEXTS.get(key, "لا يوجد شرح لهذا الإعداد حاليًا.")
        messagebox.showinfo("شرح الإعداد", text)

    def show_general_help(self):
        messagebox.showinfo(
            "شرح عام",
            "أفضل استخدام آمن:\n\n"
            "1) اختر مسار الصور.\n"
            "2) ابدأ بوضع Copy وليس Move.\n"
            "3) استخدم التصنيف المتعدد Multi Quality.\n"
            "4) اترك تقرير CSV مفعّلًا.\n"
            "5) راجع النتائج داخل فولدر sorted ثم قرر هل تحتاج Move لاحقًا."
        )

    def section(self, parent, title: str):
        frame = ttk.LabelFrame(parent, text=title, style="Section.TLabelframe")
        frame.pack(fill="x", pady=8)
        return frame

    def labeled_entry(self, parent, label: str, var_key: str, help_key: str, width: int = 20):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=6)
        ttk.Label(row, text=label, width=32).pack(side="left")
        entry = ttk.Entry(row, textvariable=self.vars[var_key], width=width)
        entry.pack(side="left", fill="x", expand=True, padx=5)
        self.help_button(row, help_key).pack(side="left")
        return entry

    def check_row(self, parent, text: str, var_key: str, help_key: str):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=5)
        check = ttk.Checkbutton(row, text=text, variable=self.vars[var_key])
        check.pack(side="left", fill="x", expand=True, anchor="w")
        self.help_button(row, help_key).pack(side="right")
        return check

    def radio_row(self, parent, text: str, value: str, var_key: str, help_key: str, command=None):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=10, pady=5)
        rb = ttk.Radiobutton(row, text=text, value=value, variable=self.vars[var_key], command=command)
        rb.pack(side="left", fill="x", expand=True, anchor="w")
        self.help_button(row, help_key).pack(side="right")
        return rb

    # =========================
    # TABS CONTENT
    # =========================

    def create_paths_section(self, parent):
        frame = self.section(parent, "المسارات الأساسية")

        self.vars["base_path"] = tk.StringVar()
        self.vars["output_folder_name"] = tk.StringVar()

        row = ttk.Frame(frame)
        row.pack(fill="x", padx=10, pady=8)
        ttk.Label(row, text="مسار الصور الأساسي:", width=32).pack(side="left")
        ttk.Entry(row, textvariable=self.vars["base_path"]).pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(row, text="اختيار", command=self.choose_base_path).pack(side="left", padx=3)
        self.help_button(row, "base_path").pack(side="left")

        self.labeled_entry(
            frame,
            "اسم فولدر النتائج:",
            "output_folder_name",
            "output_folder_name",
            width=30,
        )

        note = ttk.Label(
            frame,
            text="ملاحظة: البرنامج يتجنب فولدر النتائج تلقائيًا أثناء الفحص حتى لا يكرر معالجة الملفات.",
            foreground="#555555",
        )
        note.pack(anchor="w", padx=10, pady=(4, 10))

    def create_scan_section(self, parent):
        frame = self.section(parent, "طريقة فحص الملفات")

        self.vars["scan_mode"] = tk.StringVar()
        self.vars["numbered_prefix"] = tk.StringVar()
        self.vars["start_folder"] = tk.StringVar()
        self.vars["end_folder"] = tk.StringVar()
        self.vars["include_subfolders_in_numbered_mode"] = tk.BooleanVar()

        self.radio_row(
            frame,
            "فحص فولدرات مرقمة مثل folder_1 إلى folder_108",
            "numbered",
            "scan_mode",
            "scan_numbered",
            command=self.update_scan_mode_state,
        )
        self.radio_row(
            frame,
            "فحص كل الفولدرات الداخلية Recursive",
            "recursive",
            "scan_mode",
            "scan_recursive",
            command=self.update_scan_mode_state,
        )
        self.radio_row(
            frame,
            "فحص الملفات الموجودة مباشرة داخل المسار فقط",
            "current_only",
            "scan_mode",
            "scan_current_only",
            command=self.update_scan_mode_state,
        )

        numbered_frame = self.section(parent, "إعدادات الفولدرات الرقمية")
        self.numbered_widgets = []

        self.prefix_entry = self.labeled_entry(numbered_frame, "بادئة الفولدر:", "numbered_prefix", "numbered_prefix", width=20)
        self.start_entry = self.labeled_entry(numbered_frame, "من رقم:", "start_folder", "start_folder", width=20)
        self.end_entry = self.labeled_entry(numbered_frame, "إلى رقم:", "end_folder", "end_folder", width=20)
        self.include_subfolders_check = self.check_row(
            numbered_frame,
            "في وضع الفولدرات الرقمية: افحص الفولدرات الداخلية أيضًا",
            "include_subfolders_in_numbered_mode",
            "include_subfolders_in_numbered_mode",
        )
        self.numbered_widgets.extend([
            self.prefix_entry,
            self.start_entry,
            self.end_entry,
            self.include_subfolders_check,
        ])

    def create_operation_section(self, parent):
        frame = self.section(parent, "نوع العملية")

        self.vars["operation_mode"] = tk.StringVar()
        self.vars["prefix_source_folder"] = tk.BooleanVar()
        self.vars["keep_relative_structure"] = tk.BooleanVar()
        self.vars["skip_existing_exact"] = tk.BooleanVar()
        self.vars["process_non_images"] = tk.BooleanVar()
        self.vars["write_report"] = tk.BooleanVar()

        self.radio_row(frame, "Copy - نسخ آمن وترك الأصل كما هو", "copy", "operation_mode", "operation_copy")
        self.radio_row(frame, "Move - نقل فعلي من المكان الأصلي", "move", "operation_mode", "operation_move")

        protection = self.section(parent, "الحماية ومنع التكرار")
        self.check_row(
            protection,
            "إضافة اسم الفولدر الأصلي قبل اسم الصورة لمنع التكرار",
            "prefix_source_folder",
            "prefix_source_folder",
        )
        self.check_row(
            protection,
            "الحفاظ على الهيكل النسبي للفولدرات داخل كل تصنيف",
            "keep_relative_structure",
            "keep_relative_structure",
        )
        self.check_row(
            protection,
            "تخطي الملف إذا كان نفس الاسم موجودًا بالفعل في النتيجة",
            "skip_existing_exact",
            "skip_existing_exact",
        )
        self.check_row(
            protection,
            "نقل/نسخ الملفات غير الصور إلى فولدر non_images بدل تجاهلها",
            "process_non_images",
            "process_non_images",
        )
        self.check_row(
            protection,
            "إنشاء تقرير CSV مفصل بعد الانتهاء",
            "write_report",
            "write_report",
        )

    def create_classification_section(self, parent):
        frame = self.section(parent, "طريقة التصنيف")

        self.vars["classification_mode"] = tk.StringVar()
        self.vars["min_width"] = tk.StringVar()
        self.vars["min_height"] = tk.StringVar()
        self.vars["very_low_max_pixels"] = tk.StringVar()
        self.vars["low_max_pixels"] = tk.StringVar()
        self.vars["medium_max_pixels"] = tk.StringVar()
        self.vars["high_max_pixels"] = tk.StringVar()
        self.vars["use_min_side_downgrade"] = tk.BooleanVar()
        self.vars["very_low_min_side"] = tk.StringVar()
        self.vars["low_min_side"] = tk.StringVar()

        self.radio_row(
            frame,
            "تصنيف متعدد احترافي: very_low / low / medium / high / ultra",
            "multi_quality",
            "classification_mode",
            "classification_multi",
            command=self.update_classification_state,
        )
        self.radio_row(
            frame,
            "نفس منطق الكود الأصلي AND: low لو العرض والارتفاع أقل من الحد",
            "simple_and_640",
            "classification_mode",
            "classification_and",
            command=self.update_classification_state,
        )
        self.radio_row(
            frame,
            "منطق صارم OR: low لو العرض أو الارتفاع أقل من الحد",
            "strict_or_640",
            "classification_mode",
            "classification_or",
            command=self.update_classification_state,
        )

        self.simple_frame = self.section(parent, "إعدادات AND / OR")
        self.min_width_entry = self.labeled_entry(self.simple_frame, "الحد الأدنى للعرض:", "min_width", "min_width", width=20)
        self.min_height_entry = self.labeled_entry(self.simple_frame, "الحد الأدنى للارتفاع:", "min_height", "min_height", width=20)

        self.multi_frame = self.section(parent, "إعدادات التصنيف المتعدد")
        self.multi_widgets = []
        self.multi_widgets.append(self.labeled_entry(self.multi_frame, "very_low أقل من pixels:", "very_low_max_pixels", "very_low_max_pixels", width=20))
        self.multi_widgets.append(self.labeled_entry(self.multi_frame, "low أقل من pixels:", "low_max_pixels", "low_max_pixels", width=20))
        self.multi_widgets.append(self.labeled_entry(self.multi_frame, "medium أقل من pixels:", "medium_max_pixels", "medium_max_pixels", width=20))
        self.multi_widgets.append(self.labeled_entry(self.multi_frame, "high أقل من pixels:", "high_max_pixels", "high_max_pixels", width=20))
        self.multi_widgets.append(self.check_row(
            self.multi_frame,
            "استخدام أقل ضلع في الصورة لتخفيض التصنيف عند الصور الضيقة جدًا",
            "use_min_side_downgrade",
            "use_min_side_downgrade",
        ))
        self.multi_widgets.append(self.labeled_entry(self.multi_frame, "very_low إذا أقل ضلع من:", "very_low_min_side", "very_low_min_side", width=20))
        self.multi_widgets.append(self.labeled_entry(self.multi_frame, "low إذا أقل ضلع من:", "low_min_side", "low_min_side", width=20))

        examples = ttk.Label(
            self.multi_frame,
            text="مثال: 1920×1080 = 2,073,600 pixels → غالبًا high. صورة 300×200 = 60,000 pixels → very_low.",
            foreground="#555555",
        )
        examples.pack(anchor="w", padx=10, pady=(8, 10))

    def create_advanced_section(self, parent):
        frame = self.section(parent, "إعدادات قراءة الصور والامتدادات")

        self.vars["strict_image_validation"] = tk.BooleanVar()
        self.vars["allow_truncated_images"] = tk.BooleanVar()
        self.vars["lowercase_extensions"] = tk.BooleanVar()
        self.vars["extensions_csv"] = tk.StringVar()

        self.check_row(
            frame,
            "فحص الصور بصرامة باستخدام verify لاكتشاف الصور التالفة",
            "strict_image_validation",
            "strict_image_validation",
        )
        self.check_row(
            frame,
            "السماح بقراءة الصور غير المكتملة Truncated عند الإمكان",
            "allow_truncated_images",
            "allow_truncated_images",
        )
        self.check_row(
            frame,
            "اعتبار الامتدادات lowercase تلقائيًا",
            "lowercase_extensions",
            "lowercase_extensions",
        )
        self.labeled_entry(
            frame,
            "امتدادات الصور المسموحة:",
            "extensions_csv",
            "extensions_csv",
            width=60,
        )

    def create_settings_buttons(self, parent):
        frame = self.section(parent, "إدارة الإعدادات")

        row = ttk.Frame(frame)
        row.pack(fill="x", padx=10, pady=8)

        ttk.Button(row, text="حفظ الإعدادات", command=self.save_settings_clicked).pack(side="left", padx=3)
        self.help_button(row, "save_settings").pack(side="left", padx=(0, 8))

        ttk.Button(row, text="تحميل الإعدادات", command=self.load_settings_clicked).pack(side="left", padx=3)
        self.help_button(row, "load_settings").pack(side="left", padx=(0, 8))

        ttk.Button(row, text="استعادة الافتراضي", command=self.reset_defaults_clicked).pack(side="left", padx=3)
        self.help_button(row, "reset_defaults").pack(side="left", padx=(0, 8))

        ttk.Button(row, text="فتح فولدر النتائج", command=self.open_output_folder).pack(side="left", padx=3)
        self.help_button(row, "open_output_folder").pack(side="left", padx=(0, 8))

    def build_log_panel(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", pady=(0, 8))

        ttk.Label(top, text="سجل التشغيل", style="Title.TLabel").pack(side="left")

        self.status_var = tk.StringVar(value="جاهز")
        ttk.Label(top, textvariable=self.status_var).pack(side="right")

        self.log_text = ScrolledText(parent, height=24, wrap="word", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True)

        stats_frame = ttk.LabelFrame(parent, text="التقدم", style="Section.TLabelframe")
        stats_frame.pack(fill="x", pady=8)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(stats_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill="x", padx=10, pady=8)

        self.progress_label_var = tk.StringVar(value="0%")
        ttk.Label(stats_frame, textvariable=self.progress_label_var).pack(anchor="w", padx=10, pady=(0, 8))

    def build_bottom_bar(self, parent):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(10, 0))

        self.start_button = ttk.Button(bar, text="بدء الفرز", command=self.start_sorting, style="Success.TButton")
        self.start_button.pack(side="left", padx=5)

        self.stop_button = ttk.Button(bar, text="إيقاف", command=self.stop_sorting, style="Danger.TButton", state="disabled")
        self.stop_button.pack(side="left", padx=5)

        ttk.Button(bar, text="مسح السجل", command=self.clear_log).pack(side="left", padx=5)
        ttk.Button(bar, text="افتح تبويب السجل", command=lambda: self.notebook.select(self.log_tab)).pack(side="left", padx=5)

        ttk.Label(
            bar,
            text="نصيحة: جرّب مود نسخ الصور أولًا، وبعد مراجعة النتائج استخدم مود نقل الصور عند الحاجة.",
        ).pack(side="right")

    # =========================
    # STATE AND ACTIONS
    # =========================

    def choose_base_path(self):
        selected = filedialog.askdirectory(
            title="اختر فولدر الصور الأساسي",
            initialdir=self.vars["base_path"].get() or os.getcwd(),
        )
        if selected:
            self.vars["base_path"].set(selected)

    def set_widget_tree_state(self, widget, state: str):
        try:
            widget.configure(state=state)
        except Exception:
            pass
        for child in widget.winfo_children():
            self.set_widget_tree_state(child, state)

    def update_scan_mode_state(self):
        mode = self.vars.get("scan_mode", tk.StringVar(value="numbered")).get()
        state = "normal" if mode == "numbered" else "disabled"
        for widget in getattr(self, "numbered_widgets", []):
            self.set_widget_tree_state(widget, state)

    def update_classification_state(self):
        mode = self.vars.get("classification_mode", tk.StringVar(value="multi_quality")).get()

        simple_state = "normal" if mode in {"simple_and_640", "strict_or_640"} else "disabled"
        multi_state = "normal" if mode == "multi_quality" else "disabled"

        if hasattr(self, "simple_frame"):
            for child in self.simple_frame.winfo_children():
                self.set_widget_tree_state(child, simple_state)

        if hasattr(self, "multi_frame"):
            for child in self.multi_frame.winfo_children():
                self.set_widget_tree_state(child, multi_state)

    def log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        self.log_text.insert("end", line)
        self.log_text.see("end")

    def thread_safe_log(self, message: str):
        self.root.after(0, lambda: self.log(message))

    def clear_log(self):
        self.log_text.delete("1.0", "end")

    def thread_safe_progress(self, current: int, total: int, message: str = ""):
        def update():
            percent = (current / total * 100) if total else 0
            self.progress_var.set(percent)
            self.progress_label_var.set(f"{percent:.1f}% - {current}/{total} - {message}")
            self.status_var.set(message or "جاري المعالجة")
        self.root.after(0, update)

    def thread_safe_finish(self, success: bool, message: str):
        def update():
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self.status_var.set(message)
            self.log(message)
            self.notebook.select(self.log_tab)

            if success:
                messagebox.showinfo("انتهى", message)
            else:
                messagebox.showwarning("تنبيه", message)

        self.root.after(0, update)

    def start_sorting(self):
        if self.worker_thread and self.worker_thread.is_alive():
            messagebox.showwarning("عملية جارية", "هناك عملية فرز تعمل بالفعل.")
            return

        try:
            settings = self.collect_settings_from_ui()
            save_settings(settings)
            self.settings = settings
        except Exception as exc:
            messagebox.showerror("خطأ في الإعدادات", str(exc))
            return

        if settings.operation_mode == "move":
            confirmed = messagebox.askyesno(
                "تأكيد النقل",
                "أنت اخترت MOVE.\n\nهذا سينقل الملفات من أماكنها الأصلية.\nابدأ بـ COPY أولًا إذا لم تكن متأكدًا.\n\nهل تريد المتابعة؟"
            )
            if not confirmed:
                return

        self.output_root_last = Path(settings.base_path) / settings.output_folder_name
        self.stop_event.clear()
        self.progress_var.set(0)
        self.progress_label_var.set("0%")
        self.status_var.set("جاري البدء...")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.notebook.select(self.log_tab)

        engine = SortEngine(
            settings=settings,
            log_callback=self.thread_safe_log,
            progress_callback=self.thread_safe_progress,
            finish_callback=self.thread_safe_finish,
            stop_event=self.stop_event,
        )

        self.worker_thread = threading.Thread(target=engine.run, daemon=True)
        self.worker_thread.start()

    def stop_sorting(self):
        self.stop_event.set()
        self.status_var.set("جاري الإيقاف...")
        self.log("تم طلب إيقاف العملية. سيتم التوقف بعد الملف الحالي.")

    def collect_settings_from_ui(self) -> SortSettings:
        def get_int(key: str) -> int:
            raw = self.vars[key].get()
            try:
                return int(str(raw).replace(",", "").strip())
            except Exception:
                raise ValueError(f"القيمة غير صحيحة في الحقل: {key}")

        settings = SortSettings(
            base_path=self.vars["base_path"].get().strip(),
            output_folder_name=self.vars["output_folder_name"].get().strip() or "sorted",
            operation_mode=self.vars["operation_mode"].get(),
            scan_mode=self.vars["scan_mode"].get(),
            numbered_prefix=self.vars["numbered_prefix"].get().strip() or "folder_",
            start_folder=get_int("start_folder"),
            end_folder=get_int("end_folder"),
            classification_mode=self.vars["classification_mode"].get(),
            min_width=get_int("min_width"),
            min_height=get_int("min_height"),
            very_low_max_pixels=get_int("very_low_max_pixels"),
            low_max_pixels=get_int("low_max_pixels"),
            medium_max_pixels=get_int("medium_max_pixels"),
            high_max_pixels=get_int("high_max_pixels"),
            use_min_side_downgrade=self.vars["use_min_side_downgrade"].get(),
            low_min_side=get_int("low_min_side"),
            very_low_min_side=get_int("very_low_min_side"),
            prefix_source_folder=self.vars["prefix_source_folder"].get(),
            keep_relative_structure=self.vars["keep_relative_structure"].get(),
            process_non_images=self.vars["process_non_images"].get(),
            strict_image_validation=self.vars["strict_image_validation"].get(),
            allow_truncated_images=self.vars["allow_truncated_images"].get(),
            lowercase_extensions=self.vars["lowercase_extensions"].get(),
            write_report=self.vars["write_report"].get(),
            skip_existing_exact=self.vars["skip_existing_exact"].get(),
            include_subfolders_in_numbered_mode=self.vars["include_subfolders_in_numbered_mode"].get(),
            extensions_csv=self.vars["extensions_csv"].get().strip(),
        )

        if not settings.base_path:
            raise ValueError("يجب اختيار المسار الأساسي.")

        if settings.very_low_max_pixels > settings.low_max_pixels:
            raise ValueError("very_low_max_pixels يجب أن يكون أقل من أو يساوي low_max_pixels.")

        if settings.low_max_pixels > settings.medium_max_pixels:
            raise ValueError("low_max_pixels يجب أن يكون أقل من أو يساوي medium_max_pixels.")

        if settings.medium_max_pixels > settings.high_max_pixels:
            raise ValueError("medium_max_pixels يجب أن يكون أقل من أو يساوي high_max_pixels.")

        if settings.start_folder > settings.end_folder:
            raise ValueError("رقم بداية الفولدر لا يمكن أن يكون أكبر من رقم النهاية.")

        return settings

    def apply_settings_to_ui(self):
        settings = self.settings

        mapping = {
            "base_path": settings.base_path,
            "output_folder_name": settings.output_folder_name,
            "operation_mode": settings.operation_mode,
            "scan_mode": settings.scan_mode,
            "numbered_prefix": settings.numbered_prefix,
            "start_folder": str(settings.start_folder),
            "end_folder": str(settings.end_folder),
            "classification_mode": settings.classification_mode,
            "min_width": str(settings.min_width),
            "min_height": str(settings.min_height),
            "very_low_max_pixels": str(settings.very_low_max_pixels),
            "low_max_pixels": str(settings.low_max_pixels),
            "medium_max_pixels": str(settings.medium_max_pixels),
            "high_max_pixels": str(settings.high_max_pixels),
            "use_min_side_downgrade": settings.use_min_side_downgrade,
            "low_min_side": str(settings.low_min_side),
            "very_low_min_side": str(settings.very_low_min_side),
            "prefix_source_folder": settings.prefix_source_folder,
            "keep_relative_structure": settings.keep_relative_structure,
            "process_non_images": settings.process_non_images,
            "strict_image_validation": settings.strict_image_validation,
            "allow_truncated_images": settings.allow_truncated_images,
            "lowercase_extensions": settings.lowercase_extensions,
            "write_report": settings.write_report,
            "skip_existing_exact": settings.skip_existing_exact,
            "include_subfolders_in_numbered_mode": settings.include_subfolders_in_numbered_mode,
            "extensions_csv": settings.extensions_csv,
        }

        for key, value in mapping.items():
            var = self.vars.get(key)
            if var is not None:
                var.set(value)

    def save_settings_clicked(self):
        try:
            settings = self.collect_settings_from_ui()
            save_settings(settings)
            self.settings = settings
            self.log(f"تم حفظ الإعدادات في: {SETTINGS_FILE}")
            messagebox.showinfo("تم", "تم حفظ الإعدادات بنجاح.")
        except Exception as exc:
            messagebox.showerror("خطأ", str(exc))

    def load_settings_clicked(self):
        self.settings = load_settings()
        self.apply_settings_to_ui()
        self.update_scan_mode_state()
        self.update_classification_state()
        self.log("تم تحميل الإعدادات.")
        messagebox.showinfo("تم", "تم تحميل الإعدادات.")

    def reset_defaults_clicked(self):
        confirmed = messagebox.askyesno("استعادة الافتراضي", "هل تريد استعادة الإعدادات الافتراضية؟")
        if not confirmed:
            return

        self.settings = SortSettings()
        self.apply_settings_to_ui()
        self.update_scan_mode_state()
        self.update_classification_state()
        self.log("تمت استعادة الإعدادات الافتراضية.")

    def open_output_folder(self):
        try:
            base_path = self.vars["base_path"].get().strip()
            output_name = self.vars["output_folder_name"].get().strip() or "sorted"
            target = Path(base_path) / output_name

            if not target.exists():
                messagebox.showwarning("غير موجود", f"فولدر النتائج غير موجود بعد:\n{target}")
                return

            open_folder(target)
        except Exception as exc:
            messagebox.showerror("خطأ", str(exc))


def save_settings(settings: SortSettings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(asdict(settings), f, ensure_ascii=False, indent=2)


def load_settings() -> SortSettings:
    if not SETTINGS_FILE.exists():
        return SortSettings()

    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        defaults = asdict(SortSettings())
        defaults.update(data)
        return SortSettings(**defaults)

    except Exception:
        return SortSettings()


def open_folder(path: Path):
    path = Path(path)

    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def main():
    root = tk.Tk()
    app = ImageSorterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
