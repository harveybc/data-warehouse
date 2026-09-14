from setuptools import find_packages, setup

setup(
    name="data-warehouse-service",
    version="0.1.0",
    description="Reusable warehouse host: HTTP contract, configuration and backend interface",
    packages=find_packages(include=["data_warehouse_service", "data_warehouse_service.*"]),
    include_package_data=True,
    entry_points={"console_scripts": ["data-warehouse=data_warehouse_service.main:main"]},
    install_requires=["flask>=3.0"],
    python_requires=">=3.10",
)
