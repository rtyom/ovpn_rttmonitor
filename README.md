Для корректной работы скрипта добавьте следующие строки в файл конфигурации OpenVPN (/etc/openvpn/server.conf):

management 127.0.0.1 7505
management-client
management-hold
management-log-cache 1000

Пример полной конфигурации
----
# /etc/openvpn/server.conf
port 1194
proto udp
dev tun
ca ca.crt
cert server.crt
key server.key
dh dh.pem
server 10.8.0.0 255.255.255.0
push "redirect-gateway def1 bypass-dhcp"
push "dhcp-option DNS 8.8.8.8"
keepalive 10 120
cipher AES-256-CBC
persist-key
persist-tun
status openvpn-status.log
verb 3

# Management interface
management 127.0.0.1 7505
management-client
management-hold
management-log-cache 1000
