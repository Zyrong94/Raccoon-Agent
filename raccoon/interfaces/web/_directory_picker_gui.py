"""One-shot stdlib GUI helper. stdout is a single UTF-8 JSON response."""

import json
import sys


def main():
    root = None
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        selected = filedialog.askdirectory(parent=root, title='选择项目文件夹',
            initialdir=sys.argv[1], mustexist=True)
        result = {'path': selected or None}
    except Exception as exc:
        result = {'error': f'系统目录窗口无法打开：{exc}'}
    finally:
        if root is not None:
            root.destroy()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
