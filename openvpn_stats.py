#!/usr/bin/env python3
import socket
import time
from datetime import datetime, timedelta, timezone
import os
import json
from pathlib import Path
from collections import defaultdict

CONFIG = {
    'management_host': '127.0.0.1',
    'management_port': 7505,
    'output_html': '/var/www/html/RTi/sitemate.ru/www/vpnstat/index.html',
    'data_dir': '/var/log/openvpn_stats',
    'months_to_keep': 1,
    'total_bandwidth': 150
}

def get_local_time():
    return datetime.now(timezone(timedelta(hours=3)))

def init_environment():
    Path(CONFIG['data_dir']).mkdir(parents=True, exist_ok=True)

def get_openvpn_status():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(20)
            s.connect((CONFIG['management_host'], CONFIG['management_port']))
            banner = s.recv(1024).decode().strip()
            print(f"Получен баннер: {banner}")
            
            s.sendall(b"status 2\n\n")
            time.sleep(2)
            
            data = b""
            while True:
                try:
                    chunk = s.recv(65535)
                    if not chunk:
                        break
                    data += chunk
                    if b"END\n" in data[-100:]:
                        break
                except socket.timeout:
                    print("Таймаут при чтении данных")
                    break
            
            return data.decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"Ошибка подключения: {str(e)}")
        return None

def parse_status(raw_data):
    if not raw_data:
        return {}
    
    clients = {}
    for line in raw_data.split('\n'):
        if line.startswith('CLIENT_LIST'):
            parts = line.split(',')
            try:
                clients[parts[1]] = {
                    'real_address': parts[2],
                    'bytes_received': int(parts[5]),
                    'bytes_sent': int(parts[6]),
                    'connected_since': parts[7],
                    'timestamp': get_local_time().isoformat()
                }
            except (IndexError, ValueError) as e:
                print(f"Ошибка парсинга строки: {line}")
    return clients

def save_current_stats(current_clients):
    now = get_local_time()
    filename = Path(CONFIG['data_dir']) / f"{now.strftime('%Y-%m-%d_%H-%M')}.json"
    try:
        with open(filename, 'w') as f:
            json.dump(current_clients, f, indent=2, ensure_ascii=False)
        print(f"Сохранена текущая статистика в {filename}")
    except Exception as e:
        print(f"Ошибка сохранения текущей статистики: {e}")

def load_all_history():
    history = {}
    cutoff_date = get_local_time() - timedelta(days=30 * CONFIG['months_to_keep'])
    
    for file in Path(CONFIG['data_dir']).glob('*.json'):
        try:
            file_date = datetime.strptime(file.stem.split('_')[0], '%Y-%m-%d').replace(tzinfo=timezone(timedelta(hours=3)))
            if file_date < cutoff_date:
                file.unlink()
                continue
            with open(file, 'r') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    history[file.stem] = data
        except Exception as e:
            print(f"Ошибка обработки {file}: {e}")
    
    return history

def calculate_stats(history):
    all_users = set()
    user_stats = defaultdict(lambda: {'downloaded': 0, 'uploaded': 0, 'sessions': 0})
    hourly_stats = []
    
    for filename, day_data in history.items():
        try:
            dt = datetime.strptime(filename, '%Y-%m-%d_%H-%M').replace(tzinfo=timezone(timedelta(hours=3)))
        except ValueError:
            continue
        
        total_downloaded = 0
        total_uploaded = 0
        
        for user, data in day_data.items():
            all_users.add(user)
            downloaded = data.get('bytes_received', 0)  # Исправлено: bytes_received - это скачанные данные клиентом
            uploaded = data.get('bytes_sent', 0)         # bytes_sent - это загруженные данные клиентом
            total_downloaded += downloaded
            total_uploaded += uploaded
            user_stats[user]['downloaded'] += downloaded
            user_stats[user]['uploaded'] += uploaded
            user_stats[user]['sessions'] += 1
        
        # Рассчитываем мегабиты в секунду (5-минутный интервал)
        interval_seconds = 300  # 5 минут
        downloaded_mbps = (total_downloaded * 8) / (interval_seconds * 1000 * 1000)
        uploaded_mbps = (total_uploaded * 8) / (interval_seconds * 1000 * 1000)
        
        hourly_stats.append({
            'time': dt.strftime('%Y-%m-%d %H:%M'),
            'downloaded': round(downloaded_mbps, 2),
            'uploaded': round(uploaded_mbps, 2),
            'total': round(downloaded_mbps + uploaded_mbps, 2)
        })
    
    sorted_users = sorted(
        all_users,
        key=lambda u: user_stats[u]['downloaded'] + user_stats[u]['uploaded'],
        reverse=True
    )
    
    hourly_stats.sort(key=lambda x: x['time'])
    
    return {
        'all_users': sorted_users,
        'user_stats': user_stats,
        'hourly_stats': hourly_stats,
        'max_bandwidth': CONFIG['total_bandwidth'],
        'report_date': get_local_time().strftime('%Y-%m-%d %H:%M')
    }

def format_bytes(size):
    for unit in ['Б', 'КБ', 'МБ', 'ГБ']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} ТБ"

def generate_html_report(stats):
    time_labels = [h['time'][11:16] for h in stats['hourly_stats']] if stats['hourly_stats'] else ["Нет данных"]
    downloaded_data = [h['downloaded'] for h in stats['hourly_stats']] if stats['hourly_stats'] else [0]
    uploaded_data = [h['uploaded'] for h in stats['hourly_stats']] if stats['hourly_stats'] else [0]
    total_data = [h['total'] for h in stats['hourly_stats']] if stats['hourly_stats'] else [0]
    
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Статистика OpenVPN</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{
            background-color: #121212;
            color: #fff;
            padding: 20px;
        }}
        .total-traffic {{
            color: #ff6b6b;
        }}
        .chart-wrapper {{
            position: relative;
            height: 400px;
        }}
    </style>
</head>
<body>
<div class="container">
    <h1 class="mb-4">OpenVPN статистика</h1>
    <p>Обновлено: {stats['report_date']} (UTC+3)</p>

    <div class="card bg-dark mb-4">
        <div class="card-body">
            <div class="chart-wrapper">
                <canvas id="trafficChart"></canvas>
            </div>
        </div>
    </div>

    <div class="card bg-dark mb-4">
        <div class="card-body">
            <h5 class="card-title">Трафик по пользователям</h5>
            <div class="table-responsive">
                <table class="table table-dark table-bordered">
                    <thead>
                        <tr>
                            <th>Пользователь</th>
                            <th>Скачано</th>
                            <th>Загружено</th>
                            <th class="total-traffic">Всего</th>
                            <th>Сессий</th>
                        </tr>
                    </thead>
                    <tbody>"""
    
    for user in stats['all_users']:
        downloaded = stats['user_stats'][user]['downloaded']
        uploaded = stats['user_stats'][user]['uploaded']
        total = downloaded + uploaded
        html += f"""
                        <tr>
                            <td>{user}</td>
                            <td>{format_bytes(downloaded)}</td>
                            <td>{format_bytes(uploaded)}</td>
                            <td class="total-traffic">{format_bytes(total)}</td>
                            <td>{stats['user_stats'][user]['sessions']}</td>
                        </tr>"""
    
    html += f"""
                    </tbody>
                </table>
            </div>
        </div>
    </div>
</div>

<script>
const ctx = document.getElementById('trafficChart').getContext('2d');
new Chart(ctx, {{
    type: 'line',
    data: {{
        labels: {json.dumps(time_labels)},
        datasets: [
            {{
                label: 'Скачано (Мбит/с)',
                data: {json.dumps(downloaded_data)},
                borderColor: 'rgb(54, 162, 235)',
                backgroundColor: 'rgba(54, 162, 235, 0.2)',
                fill: true
            }},
            {{
                label: 'Загружено (Мбит/с)',
                data: {json.dumps(uploaded_data)},
                borderColor: 'rgb(75, 192, 192)',
                backgroundColor: 'rgba(75, 192, 192, 0.2)',
                fill: true
            }},
            {{
                label: 'Всего (Мбит/с)',
                data: {json.dumps(total_data)},
                borderColor: 'rgb(255, 99, 132)',
                backgroundColor: 'rgba(255, 99, 132, 0.2)',
                fill: false,
                borderWidth: 2
            }}
        ]
    }},
    options: {{
        responsive: true,
        scales: {{
            y: {{
                beginAtZero: true,
                title: {{
                    display: true,
                    text: 'Мбит/с'
                }},
                suggestedMax: {CONFIG['total_bandwidth']}
            }}
        }},
        plugins: {{
            tooltip: {{
                callbacks: {{
                    label: function(context) {{
                        return context.dataset.label + ': ' + context.raw.toFixed(2) + ' Мбит/с';
                    }}
                }}
            }}
        }}
    }}
}});
</script>

</body>
</html>
"""
    return html

def main():
    init_environment()
    
    raw_data = get_openvpn_status()
    if not raw_data:
        print("Не удалось получить данные от OpenVPN")
        return
    
    current_clients = parse_status(raw_data)
    if current_clients:
        print(f"Найдено {len(current_clients)} активных клиентов")
        save_current_stats(current_clients)
    else:
        print("Нет активных клиентов")
    
    history = load_all_history()
    stats = calculate_stats(history)
    report_html = generate_html_report(stats)
    
    try:
        with open(CONFIG['output_html'], 'w') as f:
            f.write(report_html)
        print(f"Отчет сохранен в {CONFIG['output_html']}")
    except Exception as e:
        print(f"Ошибка сохранения отчета: {e}")

if __name__ == "__main__":
    main()
    