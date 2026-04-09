import 'package:flutter/material.dart';
import '../models/pairing_result.dart';
import '../services/api_service.dart';
import 'result_screen.dart';

class HistoryScreen extends StatefulWidget {
  const HistoryScreen({super.key});

  @override
  State<HistoryScreen> createState() => _HistoryScreenState();
}

class _HistoryScreenState extends State<HistoryScreen> {
  static const _gold = Color(0xFFC9A84C);
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);

  List<PairingResponse> _history = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final data = await ApiService.getHistory();
    setState(() {
      _history = data;
      _loading = false;
    });
  }

  Future<void> _confirmClear() async {
    final confirmed = await showDialog<bool>(
      context: context,
      barrierColor: Colors.black.withOpacity(0.75),
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF1E1E1E),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(color: Colors.white.withOpacity(0.12), width: 1),
        ),
        title: const Text(
          'Очистить историю?',
          style: TextStyle(color: Colors.white, fontSize: 17, fontWeight: FontWeight.w600),
        ),
        content: Text(
          'Все ваши подборки будут удалены навсегда. Это действие нельзя отменить.',
          style: TextStyle(color: Colors.white.withOpacity(0.7), fontSize: 14, height: 1.4),
        ),
        actionsPadding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: Text(
              'Отмена',
              style: TextStyle(color: Colors.white.withOpacity(0.6), fontSize: 14),
            ),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(
              'Очистить',
              style: TextStyle(color: Colors.red.shade400, fontSize: 14, fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      final ok = await ApiService.clearHistory();
      if (ok && mounted) {
        setState(() => _history = []);
      }
    }
  }

  String _formatDate(DateTime date) {
    final now = DateTime.now();
    final diff = now.difference(date).inDays;
    if (diff == 0) return 'Сегодня';
    if (diff == 1) return 'Вчера';
    return '${date.day}.${date.month.toString().padLeft(2, '0')}.${date.year}';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      appBar: AppBar(
        backgroundColor: _bg,
        surfaceTintColor: Colors.transparent,
        title: const Text(
          'История',
          style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
        ),
        actions: _history.isNotEmpty
            ? [
                TextButton(
                  onPressed: _confirmClear,
                  child: Text('Очистить', style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 13)),
                ),
              ]
            : null,
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator(color: _gold))
          : _history.isEmpty
              ? _buildEmpty()
              : _buildList(),
    );
  }

  Widget _buildEmpty() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(40),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Icon(Icons.history_rounded, color: _gold, size: 56),
            const SizedBox(height: 20),
            const Text(
              'История пуста',
              style: TextStyle(color: Colors.white, fontSize: 18, fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 10),
            Text(
              'Все ваши подборки появятся здесь. Они хранятся 30 дней.',
              style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 14, height: 1.5),
              textAlign: TextAlign.center,
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildList() {
    return ListView.builder(
      padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
      itemCount: _history.length,
      itemBuilder: (context, index) {
        final item = _history[index];
        final showDate = index == 0 ||
            _formatDate(item.createdAt) != _formatDate(_history[index - 1].createdAt);
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (showDate) _buildDateLabel(item.createdAt),
            _buildCard(item),
          ],
        );
      },
    );
  }

  Widget _buildDateLabel(DateTime date) {
    return Padding(
      padding: const EdgeInsets.only(top: 16, bottom: 8),
      child: Text(
        _formatDate(date),
        style: TextStyle(
          color: Colors.white.withOpacity(0.3),
          fontSize: 12,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.5,
        ),
      ),
    );
  }

  String _detailLabel(String level) {
    switch (level) {
      case 'simple':
        return 'Просто';
      case 'expert':
        return 'Эксперт';
      case 'standard':
      default:
        return 'Стандарт';
    }
  }

  Widget _buildCard(PairingResponse item) {
    final firstResult = item.results.isNotEmpty ? item.results.first : null;
    final detailLabel = _detailLabel(item.detailLevel);
    return GestureDetector(
      onTap: () => Navigator.push(
        context,
        MaterialPageRoute(builder: (_) => ResultScreen(response: item)),
      ),
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: _card,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: Colors.white.withOpacity(0.05)),
        ),
        child: Row(
          children: [
            Text(
              firstResult?.resolvedEmoji ?? '🍷',
              style: const TextStyle(fontSize: 28),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    item.dish,
                    style: const TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.w600),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                  const SizedBox(height: 3),
                  // Подзаголовок: тип · бренд · режим детализации.
                  // Режим добавлен чтобы три записи "стейк рибай" в Просто/
                  // Стандарт/Эксперт визуально отличались — иначе история
                  // выглядит как дублирование одного запроса.
                  Text(
                    firstResult != null
                        ? '${firstResult.alcoholType} · ${firstResult.brand} · $detailLabel'
                        : detailLabel,
                    style: TextStyle(color: Colors.white.withOpacity(0.35), fontSize: 12),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
              decoration: BoxDecoration(
                color: item.mode == 'food_to_alcohol'
                    ? _gold.withOpacity(0.12)
                    : Colors.blue.withOpacity(0.12),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                item.mode == 'food_to_alcohol' ? '🍽️→🥂' : '🥂→🍽️',
                style: const TextStyle(fontSize: 11),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
