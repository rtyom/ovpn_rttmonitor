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
    'total_bandwidth': 150,
    'aggregation_interval': '1h'  # допустимые значения: '10m', '30m', '1h'
}

INTERVAL_SECONDS = {
    '10m': 600,
    '30m': 1800,
    '1h': 3600
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
            except (IndexError, ValueError):
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

def round_time_to_interval(dt, interval_seconds):
    seconds_since_day_start = (dt - dt.replace(hour=0, minute=0, second=0, microsecond=0)).seconds
    rounded_seconds = (seconds_since_day_start // interval_seconds) * interval_seconds
    rounded_time = dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(seconds=rounded_seconds)
    return rounded_time

def calculate_stats(history):
    all_users = set()
    user_stats_24h = defaultdict(lambda: {'downloaded': 0, 'uploaded': 0})
    user_stats_week = defaultdict(lambda: {'downloaded': 0, 'uploaded': 0})
    user_stats_month = defaultdict(lambda: {'downloaded': 0, 'uploaded': 0})
    aggregate = defaultdict(lambda: {'downloaded': [], 'uploaded': []})

    interval_key = CONFIG.get('aggregation_interval', '1h')
    interval_seconds = INTERVAL_SECONDS.get(interval_key, 3600)

    cutoff_24h = get_local_time() - timedelta(hours=24)
    cutoff_week = get_local_time() - timedelta(days=7)
    cutoff_month = get_local_time() - timedelta(days=30)

    for filename, day_data in history.items():
        try:
            file_dt = datetime.strptime(filename, '%Y-%m-%d_%H-%M').replace(tzinfo=timezone(timedelta(hours=3)))
            bucket_time = round_time_to_interval(file_dt, interval_seconds)
            bucket_key = bucket_time.strftime('%Y-%m-%d %H:%M')
        except ValueError:
            continue

        total_downloaded = 0
        total_uploaded = 0

        for user, data in day_data.items():
            all_users.add(user)
            downloaded = data.get('bytes_received', 0)
            uploaded = data.get('bytes_sent', 0)

            total_downloaded += downloaded
            total_uploaded += uploaded

            # Статистика за разные периоды
            if file_dt >= cutoff_24h:
                user_stats_24h[user]['downloaded'] += downloaded
                user_stats_24h[user]['uploaded'] += uploaded
            
            if file_dt >= cutoff_week:
                user_stats_week[user]['downloaded'] += downloaded
                user_stats_week[user]['uploaded'] += uploaded
            
            if file_dt >= cutoff_month:
                user_stats_month[user]['downloaded'] += downloaded
                user_stats_month[user]['uploaded'] += uploaded

        raw_interval_seconds = 300
        downloaded_mbps = (total_downloaded * 8) / (raw_interval_seconds * 1000 * 1000)
        uploaded_mbps = (total_uploaded * 8) / (raw_interval_seconds * 1000 * 1000)

        aggregate[bucket_key]['downloaded'].append(downloaded_mbps)
        aggregate[bucket_key]['uploaded'].append(uploaded_mbps)

    interval_stats = []
    for bucket, values in sorted(aggregate.items()):
        if values['downloaded']:
            avg_downloaded = sum(values['downloaded']) / len(values['downloaded'])
            avg_uploaded = sum(values['uploaded']) / len(values['uploaded'])
        else:
            avg_downloaded = avg_uploaded = 0

        interval_stats.append({
            'time': bucket,
            'downloaded': round(avg_downloaded, 2),
            'uploaded': round(avg_uploaded, 2),
            'total': round(avg_downloaded + avg_uploaded, 2)
        })

    # Рассчитываем итоги для каждого периода
    def calculate_totals(stats_dict):
        total_downloaded = sum(stats['downloaded'] for stats in stats_dict.values())
        total_uploaded = sum(stats['uploaded'] for stats in stats_dict.values())
        return {
            'downloaded': total_downloaded,
            'uploaded': total_uploaded,
            'total': total_downloaded + total_uploaded
        }

    return {
        'all_users': list(all_users),
        'user_stats_24h': user_stats_24h,
        'user_stats_week': user_stats_week,
        'user_stats_month': user_stats_month,
        'hourly_stats': interval_stats,
        'max_bandwidth': CONFIG['total_bandwidth'],
        'report_date': get_local_time().strftime('%Y-%m-%d %H:%M'),
        'totals': {
            '24h': calculate_totals(user_stats_24h),
            'week': calculate_totals(user_stats_week),
            'month': calculate_totals(user_stats_month)
        }
    }

def format_bytes(size):
    for unit in ['Б', 'КБ', 'МБ', 'ГБ']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} ТБ"

def generate_html_report(stats):
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
        .btn-toggle {{
            margin-bottom: 15px;
        }}
        .btn-toggle .btn.active {{
            background-color: #0d6efd;
            color: white;
        }}
        .table-totals {{
            font-weight: bold;
            background-color: #2c2c2c;
        }}
        .sortable {{
            cursor: pointer;
            position: relative;
        }}
        .sortable::after {{
            content: '';
            display: inline-block;
            margin-left: 5px;
            width: 0;
            height: 0;
            border-left: 5px solid transparent;
            border-right: 5px solid transparent;
            border-top: 5px solid #ccc;
            opacity: 0.5;
        }}
        .sortable.asc::after {{
            border-top: none;
            border-bottom: 5px solid #fff;
            opacity: 1;
        }}
        .sortable.desc::after {{
            border-top: 5px solid #fff;
            border-bottom: none;
            opacity: 1;
        }}
    </style>
</head>
<body>
<div class="container">
    <h1 class="mb-4">OpenVPN статистика</h1>
    <p>Обновлено: {stats['report_date']} (UTC+3)</p>

    <div class="btn-group btn-toggle" role="group">
        <button id="btn24h" type="button" class="btn btn-primary active" onclick="switchView('24h')">Последние 24 часа</button>
        <button id="btnWeek" type="button" class="btn btn-secondary" onclick="switchView('week')">Последняя неделя</button>
        <button id="btnMonth" type="button" class="btn btn-secondary" onclick="switchView('month')">Последний месяц</button>
    </div>

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
                            <th class="sortable" onclick="sortTable('user')">Пользователь</th>
                            <th class="sortable" onclick="sortTable('tx')">Tx</th>
                            <th class="sortable" onclick="sortTable('rx')">Rx</th>
                            <th class="sortable total-traffic" onclick="sortTable('total')">Всего</th>
                        </tr>
                    </thead>
                    <tbody id="userTableBody">
                    </tbody>
                    <tfoot>
                        <tr class="table-totals">
                            <td>Всего</td>
                            <td id="totalTx"></td>
                            <td id="totalRx"></td>
                            <td id="totalAll" class="total-traffic"></td>
                        </tr>
                    </tfoot>
                </table>
            </div>
        </div>
    </div>
</div>

<script>
const rawData = {json.dumps(stats['hourly_stats'])};
const userStats = {{
    '24h': {json.dumps(stats['user_stats_24h'])},
    'week': {json.dumps(stats['user_stats_week'])},
    'month': {json.dumps(stats['user_stats_month'])}
}};
const totals = {json.dumps(stats['totals'])};

let currentView = '24h';
let sortField = 'total';
let sortDirection = 'desc';

function filterData(days) {{
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() - days);
    return rawData.filter(item => new Date(item.time) >= cutoff);
}}

function aggregateData(data, intervalHours) {{
    const buckets = {{}};
    data.forEach(item => {{
        const dt = new Date(item.time);
        const bucketKey = new Date(Math.floor(dt.getTime() / (intervalHours*3600*1000)) * intervalHours*3600*1000);
        const key = bucketKey.toISOString();
        if (!buckets[key]) {{
            buckets[key] = {{downloaded: [], uploaded: [], total: []}};
        }}
        buckets[key].downloaded.push(item.downloaded);
        buckets[key].uploaded.push(item.uploaded);
        buckets[key].total.push(item.total);
    }});
    return Object.entries(buckets).map(([time, values]) => {{
        return {{
            time: time,
            downloaded: values.downloaded.reduce((a,b)=>a+b,0)/values.downloaded.length,
            uploaded: values.uploaded.reduce((a,b)=>a+b,0)/values.uploaded.length,
            total: values.total.reduce((a,b)=>a+b,0)/values.total.length
        }}
    }}).sort((a,b)=> new Date(a.time)-new Date(b.time));
}}

function formatLabel(timeString, viewMode) {{
    const dt = new Date(timeString);
    if (viewMode === '24h') {{
        return dt.toLocaleTimeString('ru-RU', {{hour: '2-digit', minute: '2-digit'}});
    }} else {{
        return dt.toLocaleDateString('ru-RU', {{day: '2-digit', month: '2-digit'}});
    }}
}}

function renderChart(data, viewMode) {{
    const ctx = document.getElementById('trafficChart').getContext('2d');
    if (window.chartInstance) {{
        window.chartInstance.destroy();
    }}
    window.chartInstance = new Chart(ctx, {{
        type: 'line',
        data: {{
            labels: data.map(h => formatLabel(h.time, viewMode)),
            datasets: [
                {{
                    label: 'Tx (Мбит/с)',
                    data: data.map(h => h.downloaded),
                    borderColor: 'rgb(54, 162, 235)',
                    backgroundColor: 'rgba(54, 162, 235, 0.2)',
                    fill: true
                }},
                {{
                    label: 'Rx (Мбит/с)',
                    data: data.map(h => h.uploaded),
                    borderColor: 'rgb(75, 192, 192)',
                    backgroundColor: 'rgba(75, 192, 192, 0.2)',
                    fill: true
                }},
                {{
                    label: 'Всего (Мбит/с)',
                    data: data.map(h => h.total),
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
}}

function formatBytes(size) {{
    const units = ['Б', 'КБ', 'МБ', 'ГБ'];
    let i = 0;
    while (size >= 1024 && i < units.length - 1) {{
        size /= 1024;
        i++;
    }}
    return size.toFixed(2) + ' ' + units[i];
}}

function updateUserTable() {{
    const tbody = document.getElementById('userTableBody');
    tbody.innerHTML = '';
    
    const currentStats = userStats[currentView];
    const users = Object.keys(currentStats);
    
    // Сортировка
    users.sort((a, b) => {{
        let valueA, valueB;
        
        switch(sortField) {{
            case 'user':
                valueA = a.toLowerCase();
                valueB = b.toLowerCase();
                break;
            case 'tx':
                valueA = currentStats[a].downloaded;
                valueB = currentStats[b].downloaded;
                break;
            case 'rx':
                valueA = currentStats[a].uploaded;
                valueB = currentStats[b].uploaded;
                break;
            case 'total':
                valueA = currentStats[a].downloaded + currentStats[a].uploaded;
                valueB = currentStats[b].downloaded + currentStats[b].uploaded;
                break;
        }}
        
        if (sortDirection === 'asc') {{
            return valueA > valueB ? 1 : -1;
        }} else {{
            return valueA < valueB ? 1 : -1;
        }}
    }});
    
    // Заполнение таблицы
    users.forEach(user => {{
        const downloaded = currentStats[user].downloaded;
        const uploaded = currentStats[user].uploaded;
        const total = downloaded + uploaded;
        
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${{user}}</td>
            <td>${{formatBytes(downloaded)}}</td>
            <td>${{formatBytes(uploaded)}}</td>
            <td class="total-traffic">${{formatBytes(total)}}</td>
        `;
        tbody.appendChild(row);
    }});
    
    // Обновление итогов
    document.getElementById('totalTx').textContent = formatBytes(totals[currentView].downloaded);
    document.getElementById('totalRx').textContent = formatBytes(totals[currentView].uploaded);
    document.getElementById('totalAll').textContent = formatBytes(totals[currentView].total);
}}

function sortTable(field) {{
    if (sortField === field) {{
        sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
    }} else {{
        sortField = field;
        sortDirection = 'desc';
    }}
    
    // Обновление стрелочек сортировки
    document.querySelectorAll('.sortable').forEach(el => {{
        el.classList.remove('asc', 'desc');
    }});
    const header = document.querySelector(`.sortable[onclick="sortTable('${{sortField}}')"]`);
    if (header) {{
        header.classList.add(sortDirection);
    }}
    
    updateUserTable();
}}

function switchView(view) {{
    currentView = view;
    
    // Обновление активной кнопки
    document.getElementById('btn24h').classList.remove('active');
    document.getElementById('btnWeek').classList.remove('active');
    document.getElementById('btnMonth').classList.remove('active');
    document.getElementById('btn' + view.charAt(0).toUpperCase() + view.slice(1)).classList.add('active');
    
    // Обновление графика
    let chartData;
    if (view === '24h') {{
        chartData = filterData(1);
    }} else if (view === 'week') {{
        const weekData = filterData(7);
        chartData = aggregateData(weekData, 6);
    }} else {{
        const monthData = filterData(30);
        chartData = aggregateData(monthData, 24);
    }}
    renderChart(chartData, view);
    
    // Обновление таблицы
    updateUserTable();
}}

// Инициализация
document.addEventListener('DOMContentLoaded', function() {{
    // Устанавливаем начальную сортировку
    sortTable('total');
    switchView('24h');
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
    