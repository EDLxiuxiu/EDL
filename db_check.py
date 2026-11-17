from getpass import getpass

import mysql.connector
from mysql.connector import Error


def main():
    password = getpass("请输入 MySQL 应用账号 admission_app 的密码：")

    try:
        with mysql.connector.connect(
            host="127.0.0.1",
            port=3306,
            user="admission_app",
            password=password,
            database="admissionsim",
            connection_timeout=5,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT VERSION(), DATABASE(), CURRENT_USER(), 1")
                version, database, account, probe = cursor.fetchone()
    except Error as exc:
        raise SystemExit(f"MySQL 验证失败（{exc.errno}）：{exc.msg}") from None

    print("MySQL 连接成功")
    print(f"服务器版本 {version}")
    print(f"当前数据库 {database}")
    print(f"认证账号 {account}")
    print(f"查询结果 {probe}")


if __name__ == "__main__":
    main()