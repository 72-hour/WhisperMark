"""
图片处理工具模块
处理学生答案图片的旋转、显示等
"""
import base64
import io
from pathlib import Path
from typing import Optional, Tuple

try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def get_image_orientation(img: "Image.Image") -> int:
    """
    根据EXIF信息获取图片方向
    
    Args:
        img: PIL Image对象
        
    Returns:
        旋转角度（0, 90, 180, 270）
    """
    if not HAS_PIL:
        return 0
    
    try:
        exif = img.getexif()
        if exif:
            # 274是Orientation标签
            orientation = exif.get(274, 1)
            orientation_map = {
                1: 0,
                2: 0,  # 镜像
                3: 180,
                4: 180,  # 镜像
                5: 270,  # 镜像
                6: 270,
                7: 90,  # 镜像
                8: 90,
            }
            return orientation_map.get(orientation, 0)
    except Exception:
        pass
    
    return 0


def auto_rotate_image(image_data: bytes) -> bytes:
    """
    自动旋转图片到正确方向
    
    Args:
        image_data: 图片二进制数据
        
    Returns:
        旋转后的图片二进制数据
    """
    if not HAS_PIL:
        return image_data
    
    try:
        img = Image.open(io.BytesIO(image_data))
        
        # 使用ImageOps自动处理EXIF方向
        img = ImageOps.exif_transpose(img)
        
        # 保存为JPEG
        output = io.BytesIO()
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(output, format='JPEG', quality=95)
        return output.getvalue()
    except Exception:
        return image_data


def rotate_image(image_data: bytes, angle: int) -> bytes:
    """
    按指定角度旋转图片
    
    Args:
        image_data: 图片二进制数据
        angle: 旋转角度（90, 180, 270）
        
    Returns:
        旋转后的图片二进制数据
    """
    if not HAS_PIL or angle == 0:
        return image_data
    
    try:
        img = Image.open(io.BytesIO(image_data))
        # expand=True确保旋转后图片完整显示
        img = img.rotate(angle, expand=True)
        
        output = io.BytesIO()
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(output, format='JPEG', quality=95)
        return output.getvalue()
    except Exception:
        return image_data


def get_image_size(image_data: bytes) -> Tuple[int, int]:
    """
    获取图片尺寸
    
    Args:
        image_data: 图片二进制数据
        
    Returns:
        (width, height) 元组
    """
    if not HAS_PIL:
        return (0, 0)
    
    try:
        img = Image.open(io.BytesIO(image_data))
        return img.size
    except Exception:
        return (0, 0)


def image_to_base64(image_data: bytes) -> str:
    """
    将图片二进制数据转为base64字符串
    
    Args:
        image_data: 图片二进制数据
        
    Returns:
        base64编码字符串
    """
    return base64.b64encode(image_data).decode('utf-8')


def save_image(image_data: bytes, save_path: Path) -> bool:
    """
    保存图片到文件
    
    Args:
        image_data: 图片二进制数据
        save_path: 保存路径
        
    Returns:
        是否保存成功
    """
    try:
        save_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, 'wb') as f:
            f.write(image_data)
        return True
    except Exception:
        return False


def resize_image(image_data: bytes, max_width: int = 800) -> bytes:
    """
    等比例缩放图片
    
    Args:
        image_data: 图片二进制数据
        max_width: 最大宽度
        
    Returns:
        缩放后的图片二进制数据
    """
    if not HAS_PIL:
        return image_data
    
    try:
        img = Image.open(io.BytesIO(image_data))
        width, height = img.size
        
        if width <= max_width:
            return image_data
        
        # 计算等比例高度
        new_height = int(height * max_width / width)
        img = img.resize((max_width, new_height), Image.LANCZOS)
        
        output = io.BytesIO()
        if img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        img.save(output, format='JPEG', quality=90)
        return output.getvalue()
    except Exception:
        return image_data
