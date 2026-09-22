"""
文件分类整理脚本
将多个项目文件夹中相同类型的文件归类到统一的支持性文件目录下
"""

import os
import shutil
from pathlib import Path


def organize_files_by_category(source_dir, target_dir):
    """
    按类别整理文件

    Args:
        source_dir: 源目录路径（包含多个项目文件夹）
        target_dir: 目标目录路径（支持性文件目录）
    """
    source_path = Path(source_dir)
    target_path = Path(target_dir)

    # 确保目标目录存在（如果不存在则创建）
    target_path.mkdir(parents=True, exist_ok=True)

    # 统计信息
    stats = {
        'total_files': 0,
        'copied_files': 0,
        'categories': {}
    }

    # 遍历源目录下的所有项目文件夹
    for project_folder in source_path.iterdir():
        if not project_folder.is_dir():
            continue

        print(f"\n处理项目: {project_folder.name}")

        # 遍历项目文件夹下的所有分类文件夹
        for category_folder in project_folder.iterdir():
            if not category_folder.is_dir():
                continue

            category_name = category_folder.name
            print(f"  - 分类: {category_name}")

            # 在目标目录中使用对应的分类文件夹（如果已存在直接使用，不存在则创建）
            target_category_path = target_path / category_name
            target_category_path.mkdir(exist_ok=True)

            # 初始化该分类的统计
            if category_name not in stats['categories']:
                stats['categories'][category_name] = 0

            # 遍历分类文件夹中的所有文件
            for file_path in category_folder.rglob('*'):
                if file_path.is_file():
                    stats['total_files'] += 1

                    # 构建目标文件路径
                    target_file_path = target_category_path / file_path.name

                    # 如果文件已存在，添加序号避免覆盖
                    if target_file_path.exists():
                        base_name = file_path.stem
                        suffix = file_path.suffix
                        counter = 1

                        while target_file_path.exists():
                            new_name = f"{base_name}_{counter}{suffix}"
                            target_file_path = target_category_path / new_name
                            counter += 1

                    try:
                        # 复制文件
                        shutil.copy2(file_path, target_file_path)
                        stats['copied_files'] += 1
                        stats['categories'][category_name] += 1
                        print(f"    ✓ {file_path.name}")
                    except Exception as e:
                        print(f"    ✗ {file_path.name} - 错误: {e}")

    # 打印统计信息
    print("\n" + "="*60)
    print("整理完成！统计信息:")
    print("="*60)
    print(f"总文件数: {stats['total_files']}")
    print(f"成功复制: {stats['copied_files']}")
    print(f"\n各分类文件数量:")
    for category, count in sorted(stats['categories'].items()):
        print(f"  {category}: {count} 个文件")
    print("="*60)

    return stats


def main():
    """主函数"""
    # 配置路径
    source_directory = r"C:\Users\EDY\Desktop\识别pdf\发展中心资料库\发展中心资料库"  # 源目录：包含多个项目的父目录
    target_directory = r"C:\Users\EDY\Desktop\PDF\支持性文件"      # 目标目录：支持性文件目录

    print("开始整理文件...")
    print(f"源目录: {source_directory}")
    print(f"目标目录: {target_directory}")
    print("-"*60)

    # 执行整理
    if os.path.exists(source_directory):
        organize_files_by_category(source_directory, target_directory)
        print(f"\n整理完成！文件已保存到: {target_directory}")
    else:
        print(f"错误: 源目录不存在 - {source_directory}")
        print("请检查并修改脚本中的 source_directory 路径")


if __name__ == "__main__":
    main()
