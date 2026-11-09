"""
图片处理工具模块
处理学生答案图片的旋转、缩放、base64 转换等
"""
import base64
import io
from pathlib import Path
from typing import Optional, Tuple

try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except ImportError:  # pragma: no cover
    HAS_PIL = False


def auto_rotate_image(image_data: bytes) -> bytes:
    """自动根据 EXIF 信息校正图片方向，并统一转为 JPEG"""
    if not HAS_PIL:
        return image_data
    try:
        img = Image.open(io.BytesIO(image_data))
        img = ImageOps.exif_transpose(img)
        output = io.BytesIO()
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(output, format="JPEG", quality=95)
        return output.getvalue()
    except Exception:
        return image_data


def rotate_image(image_data: bytes, angle: int) -> bytes:
    """按指定角度旋转图片"""
    if not HAS_PIL or angle == 0:
        return image_data
    try:
        img = Image.open(io.BytesIO(image_data))
        img = img.rotate(angle, expand=True)
        output = io.BytesIO()
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(output, format="JPEG", quality=95)
        return output.getvalue()
    except Exception:
        return image_data


def get_image_size(image_data: bytes) -> Tuple[int, int]:
    """获取图片尺寸 (width, height)"""
    if not HAS_PIL:
        return (0, 0)
    try:
        img = Image.open(io.BytesIO(image_data))
        return img.size
    except Exception:
        return (0, 0)


def image_to_base64(image_data: bytes) -> str:
    """图片二进制转 base64 字符串"""
    return base64.b64encode(image_data).decode("utf-8")


def save_image(image_data: bytes, save_path: Path) -> bool:
    """保存图片到文件"""
    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(image_data)
        return True
    except Exception:
        return False


def resize_image(image_data: bytes, max_width: int = 800) -> bytes:
    """等比例缩放图片（超宽时压缩，节省 token）"""
    if not HAS_PIL:
        return image_data
    try:
        img = Image.open(io.BytesIO(image_data))
        width, height = img.size
        if width <= max_width:
            return image_data
        new_height = int(height * max_width / width)
        img = img.resize((max_width, new_height), Image.LANCZOS)
        output = io.BytesIO()
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(output, format="JPEG", quality=90)
        return output.getvalue()
    except Exception:
        return image_data
