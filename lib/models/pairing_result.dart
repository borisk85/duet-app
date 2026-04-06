class PairingResult {
  final String alcoholType;
  final String alcoholTypeEmoji;
  final String name;
  final String brand;
  final String reason;
  final String priceRange;
  final String servingTip;
  // Существует только в Эксперт-режиме. Отдельный блок гастрономической логики.
  // В Просто и Стандарт = null, и UI не рендерит блок.
  final String? whyItWorks;

  const PairingResult({
    required this.alcoholType,
    required this.alcoholTypeEmoji,
    required this.name,
    required this.brand,
    required this.reason,
    required this.priceRange,
    required this.servingTip,
    this.whyItWorks,
  });

  factory PairingResult.fromJson(Map<String, dynamic> json) {
    final whyRaw = json['why_it_works'];
    return PairingResult(
      alcoholType: json['alcohol_type'] ?? '',
      alcoholTypeEmoji: json['alcohol_type_emoji'] ?? '🍷',
      name: json['name'] ?? '',
      brand: json['brand'] ?? '',
      reason: json['reason'] ?? '',
      priceRange: json['price_range'] ?? '',
      servingTip: json['serving_tip'] ?? '',
      whyItWorks: (whyRaw is String && whyRaw.trim().isNotEmpty) ? whyRaw : null,
    );
  }

  String get resolvedEmoji {
    final t = alcoholType.toLowerCase();
    if (t.contains('белое') || t.contains('white')) return '🥂';
    if (t.contains('розовое') || t.contains('rose')) return '🌸';
    if (t.contains('красное') || t.contains('red wine')) return '🍷';
    if (t.contains('игристое') || t.contains('шампан') || t.contains('prosecco') || t.contains('cava')) return '🍾';
    if (t.contains('виски') || t.contains('whisky') || t.contains('whiskey') || t.contains('коньяк') || t.contains('бренди')) return '🥃';
    if (t.contains('пиво') || t.contains('beer') || t.contains('lager') || t.contains('ale')) return '🍺';
    if (t.contains('водка') || t.contains('vodka')) return '🫗';
    if (t.contains('джин') || t.contains('gin')) return '🌿';
    if (t.contains('ром') || t.contains('rum')) return '🍹';
    if (t.contains('текила') || t.contains('tequila')) return '🌵';
    if (t.contains('коктейл') || t.contains('cocktail')) return '🍸';
    return alcoholTypeEmoji;
  }

  Map<String, dynamic> toJson() => {
    'alcohol_type': alcoholType,
    'alcohol_type_emoji': alcoholTypeEmoji,
    'name': name,
    'brand': brand,
    'reason': reason,
    'price_range': priceRange,
    'serving_tip': servingTip,
    if (whyItWorks != null) 'why_it_works': whyItWorks,
  };
}

class PairingResponse {
  final int? id; // id из БД (для удаления из избранного)
  final String dish;
  final String mode;
  final String budget;
  final String region;
  final List<PairingResult> results;
  final DateTime createdAt;

  PairingResponse({
    this.id,
    required this.dish,
    required this.mode,
    required this.budget,
    this.region = 'СНГ',
    required this.results,
    required this.createdAt,
  });

  factory PairingResponse.fromJson(Map<String, dynamic> json) {
    return PairingResponse(
      id: json['id'] as int?,
      dish: json['dish'] ?? '',
      mode: json['mode'] ?? 'food_to_alcohol',
      budget: json['budget'] ?? 'medium',
      region: json['region'] ?? 'СНГ',
      results: (json['results'] as List<dynamic>? ?? [])
          .map((r) => PairingResult.fromJson(r))
          .toList(),
      createdAt: DateTime.tryParse(json['created_at'] ?? '') ?? DateTime.now(),
    );
  }

  Map<String, dynamic> toJson() => {
    'dish': dish,
    'mode': mode,
    'budget': budget,
    'region': region,
    'results': results.map((r) => r.toJson()).toList(),
    'created_at': createdAt.toIso8601String(),
  };
}
