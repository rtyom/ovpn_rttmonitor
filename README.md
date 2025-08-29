Для корректной работы скрипта добавьте следующие строки в файл конфигурации OpenVPN (/etc/openvpn/server.conf):

management 127.0.0.1 7505
management-client
management-hold
management-log-cache 1000
