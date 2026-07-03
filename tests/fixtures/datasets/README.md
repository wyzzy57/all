# 数据集测试 fixture

本目录不提交二进制图片或大文件。

`tests/integration/test_dataset_upload.py` 使用 Pillow 在测试运行时动态生成小图片和 zip 文件，避免 fixture 文件与实现细节耦合。
