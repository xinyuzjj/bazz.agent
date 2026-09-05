"""图片文字识别（OCR）—— RapidOCR(PP-OCRv4) 离线引擎，懒加载单例。

用途：用户往输入栏贴图/发图时，若当前 LLM 不支持视觉，则先用本模块把
图片里的文字（中文/英文/数字）提出来拼进用户消息，模型照常能读到内容。

- 纯本地推理（onnxruntime），无网络依赖、不上传图片；
- 首次调用才 import 并初始化（避免拖慢启动、避免无图场景装依赖报错）；
- 任何失败都抛 OCRError，由上层降级为「图片已附但无法识别」而不是崩会话。
"""
import base64
import os
import threading
import traceback

_engine = None
_lock = threading.Lock()
_last_error: str = ""


class OCRError(Exception):
    pass


def _get_engine():
    """懒加载 RapidOCR 单例（线程安全）。"""
    global _engine
    if _engine is not None:
        return _engine
    with _lock:
        if _engine is not None:
            return _engine
        try:
            from rapidocr_onnxruntime import RapidOCR
            # warm: 一些版本构造时不加载模型，首次推理才 load；这里显式探活一次
            _engine = RapidOCR()
            # 用一个 1x1 空白图触发内部模型加载（若模型缺失立刻暴露）
            import numpy as np
            _engine(np.zeros((4, 4, 3), dtype=np.uint8))
        except Exception as e:
            _engine = None
            raise OCRError(f"OCR 引擎不可用：{type(e).__name__}: {str(e)[:160]}")
    return _engine


def _decode_image(data: bytes) -> "object":
    """bytes → numpy BGR 数组（RapidOCR 输入格式）。"""
    try:
        import numpy as np
    except Exception as e:
        raise OCRError(f"缺少 numpy: {e}")
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        # PNG：用 PIL 解码最稳（支持 16bit/调色板）
        try:
            from PIL import Image
            import io
            img = Image.open(io.BytesIO(data)).convert("RGB")
            return np.asarray(img)[:, :, ::-1].copy()  # RGB→BGR
        except Exception:
            pass
    # JPEG/BMP/WebP 等：优先 cv2.imdecode（快），失败再回 PIL
    try:
        import cv2
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is not None:
            return img
    except Exception:
        pass
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data)).convert("RGB")
        return np.asarray(img)[:, :, ::-1].copy()
    except Exception as e:
        raise OCRError(f"图片解码失败: {e}")


def ocr_bytes(data: bytes, max_side: int = 1600) -> str:
    """识别图片 bytes 里的文字，按视觉顺序（自上而下、行内自左而右）拼成文本。

    max_side：超过该边长的图先等比缩小（提速且小字更稳）。
    """
    global _last_error
    if not data:
        raise OCRError("空图片数据")
    try:
        eng = _get_engine()
        img = _decode_image(data)
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            try:
                import cv2
                scale = max_side / max(h, w)
                img = cv2.resize(img, (int(w * scale), int(h * scale)),
                                 interpolation=cv2.INTER_AREA)
            except Exception:
                pass
        result, _ = eng(img)
        if not result:
            return ""
        # result: [[box(4点), text, score], ...] → 按行分组：取文字框中心 y，相近 y 合并为同一行
        items = []
        for box, text, score in result:
            if not text or not str(text).strip():
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cx = sum(xs) / 4
            cy = sum(ys) / 4
            items.append((cy, cx, str(text).strip()))
        items.sort(key=lambda t: t[0])  # 按 y
        # 行聚合：y 差 < 行高中位数*0.6 视为同行
        lines: list[list] = []
        for cy, cx, txt in items:
            if not lines:
                lines.append([(cy, cx, txt)])
                continue
            if abs(cy - sum(it[0] for it in lines[-1]) / len(lines[-1])) < 14:
                lines[-1].append((cy, cx, txt))
            else:
                lines.append([(cy, cx, txt)])
        out_lines = []
        for ln in lines:
            ln.sort(key=lambda t: t[1])  # 同行按 x 排序
            out_lines.append(" ".join(t[2] for t in ln))
        return "\n".join(out_lines)
    except OCRError:
        raise
    except Exception as e:
        _last_error = traceback.format_exc()
        raise OCRError(f"OCR 识别失败: {type(e).__name__}: {str(e)[:160]}")


def ocr_data_url(data_url: str) -> str:
    """识别 data:image/...;base64,xxxx 形式的图片。"""
    try:
        b64 = data_url.split(",", 1)[1] if "," in data_url else data_url
        return ocr_bytes(base64.b64decode(b64))
    except (IndexError, ValueError) as e:
        raise OCRError(f"dataURL 解析失败: {e}")


if __name__ == "__main__":
    import sys
    # 自检：读入一张命令行给的文件（png/jpg）并打印识别文本
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        with open(sys.argv[1], "rb") as f:
            print("OCR:\n" + (ocr_bytes(f.read()) or "(无文字)"))
    else:
        print("用法: python ocr_engine.py <image.png|jpg>")
