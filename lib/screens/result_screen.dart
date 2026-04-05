import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';
import '../models/pairing_result.dart';

class ResultScreen extends StatelessWidget {
  final PairingResponse response;
  final VoidCallback? onSave;

  const ResultScreen({
    super.key,
    required this.response,
    this.onSave,
  });

  static const _gold = Color(0xFFC9A84C);
  static const _goldText = Color(0xFFD4B563); // AA контраст на тёмном фоне
  static const _bg = Color(0xFF0D0D0D);
  static const _card = Color(0xFF1A1A1A);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _bg,
      appBar: _buildAppBar(context),
      body: ListView(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
        children: [
          _buildDishHeader(),
          const SizedBox(height: 24),
          _buildResultsLabel(),
          const SizedBox(height: 12),
          ...response.results.asMap().entries.map(
            (e) => _buildResultCard(e.key + 1, e.value),
          ),
          const SizedBox(height: 24),
          _buildSaveButton(context),
          const SizedBox(height: 32),
        ],
      ),
    );
  }

  PreferredSizeWidget _buildAppBar(BuildContext context) {
    return AppBar(
      backgroundColor: _bg,
      surfaceTintColor: Colors.transparent,
      leading: GestureDetector(
        onTap: () => Navigator.pop(context),
        child: const Icon(Icons.arrow_back_ios_rounded, color: Colors.white54, size: 20),
      ),
      title: const Text(
        'Дуэт',
        style: TextStyle(color: _gold, fontSize: 18, fontWeight: FontWeight.w700, letterSpacing: 1),
      ),
      centerTitle: true,
      actions: [
        GestureDetector(
          onTap: () {
            HapticFeedback.lightImpact();
            onSave?.call();
          },
          child: const Padding(
            padding: EdgeInsets.only(right: 20),
            child: Icon(Icons.star_border_rounded, color: Colors.white38, size: 24),
          ),
        ),
      ],
    );
  }

  Widget _buildDishHeader() {
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: _card,
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: Colors.white.withOpacity(0.06)),
      ),
      child: Row(
        children: [
          Text(
            response.mode == 'food_to_alcohol' ? '🍽️' : '🥂',
            style: const TextStyle(fontSize: 28),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  response.mode == 'food_to_alcohol' ? 'Блюдо' : 'Напиток',
                  style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 12),
                ),
                const SizedBox(height: 2),
                Text(
                  response.dish,
                  style: const TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 4),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: _gold.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(
                    response.budget == 'budget' ? '💰 Бюджетно'
                        : response.budget == 'premium' ? '💰💰💰 Премиум'
                        : '💰💰 Средний',
                    style: const TextStyle(color: _gold, fontSize: 11, fontWeight: FontWeight.w600),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildResultsLabel() {
    return Text(
      response.mode == 'food_to_alcohol' ? 'Подходящие напитки' : 'Подходящие блюда',
      style: TextStyle(
        color: Colors.white.withOpacity(0.5),
        fontSize: 13,
        fontWeight: FontWeight.w500,
        letterSpacing: 0.5,
      ),
    );
  }

  Widget _buildResultCard(int index, PairingResult result) {
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: BoxDecoration(
        color: _card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
          color: index == 1 ? _gold.withOpacity(0.4) : Colors.white.withOpacity(0.06),
          width: index == 1 ? 1.5 : 1,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _buildCardHeader(index, result),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  result.reason,
                  style: TextStyle(color: Colors.white.withOpacity(0.7), fontSize: 14, height: 1.5),
                ),
                const SizedBox(height: 12),
                _buildBottomRow(result),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildCardHeader(int index, PairingResult result) {
    return Padding(
      padding: const EdgeInsets.all(16),
      child: Row(
        children: [
          Container(
            width: 32,
            height: 32,
            decoration: BoxDecoration(
              color: index == 1 ? _gold : Colors.white.withOpacity(0.08),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Center(
              child: Text(
                '$index',
                style: TextStyle(
                  color: index == 1 ? _bg : Colors.white38,
                  fontSize: 14,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(result.alcoholTypeEmoji, style: const TextStyle(fontSize: 14)),
                    const SizedBox(width: 4),
                    Text(
                      result.alcoholType,
                      style: TextStyle(color: Colors.white.withOpacity(0.4), fontSize: 12),
                    ),
                  ],
                ),
                const SizedBox(height: 2),
                Text(
                  result.name,
                  style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.w700),
                ),
                GestureDetector(
                  onTap: () => _openBuyLink(result.brand),
                  child: Row(
                    children: [
                      Text(
                        result.brand,
                        style: const TextStyle(color: _goldText, fontSize: 13),
                      ),
                      const SizedBox(width: 4),
                      const Icon(Icons.open_in_new_rounded, size: 12, color: _goldText),
                    ],
                  ),
                ),
              ],
            ),
          ),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
            decoration: BoxDecoration(
              color: Colors.white.withOpacity(0.05),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text(
              result.priceRange,
              style: TextStyle(color: Colors.white.withOpacity(0.5), fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBottomRow(PairingResult result) {
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.04),
        borderRadius: BorderRadius.circular(10),
      ),
      child: Row(
        children: [
          const Text('💡', style: TextStyle(fontSize: 14)),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              result.servingTip,
              style: TextStyle(color: Colors.white.withOpacity(0.5), fontSize: 13, height: 1.4),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _openBuyLink(String brand) async {
    final cityMap = {
      'Казахстан': 'Алматы',
      'Россия': 'Москва',
      'Украина': 'Киев',
      'Беларусь': 'Минск',
    };
    final city = cityMap[response.region] ?? '';
    final query = Uri.encodeComponent('$brand купить${city.isNotEmpty ? ' $city' : ''}');
    final uri = Uri.parse('https://www.google.com/search?q=$query');
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  Widget _buildSaveButton(BuildContext context) {
    return SizedBox(
      width: double.infinity,
      height: 54,
      child: ElevatedButton.icon(
        onPressed: () {
          HapticFeedback.mediumImpact();
          onSave?.call();
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: const Text('Сохранено в избранное'),
              backgroundColor: _gold,
              behavior: SnackBarBehavior.floating,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
            ),
          );
        },
        style: ElevatedButton.styleFrom(
          backgroundColor: _gold,
          foregroundColor: _bg,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          elevation: 0,
        ),
        icon: const Icon(Icons.star_rounded, size: 20),
        label: const Text(
          'Сохранить в избранное',
          style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
        ),
      ),
    );
  }
}
