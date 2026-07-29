"""
Parser inteligente para planilhas orçamentárias
Extrai e normaliza dados de tabelas de orçamento sem depender de IA
"""

import re
from typing import List, Dict, Any, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class BudgetParser:
    """Parser robusto para extração de dados de orçamentos"""
    
    # Palavras-chave para identificar colunas
    DESCRICAO_KEYWORDS = [
        'descrição', 'descricao', 'description', 'descr', 'serviço', 'servico',
        'do serviço', 'do servico', 'material', 'especificação', 'especificacao',
    ]
    QUANTIDADE_KEYWORDS = [
        'qtd', 'quant', 'quantidade', 'quantity', 'qty', 'qtde',
        'qtde. máxima', 'qtde máxima', 'qtde maxima', 'qtde. maxima',
    ]
    UNIDADE_KEYWORDS = ['un', 'und', 'unid', 'unidade', 'unit', 'u.', 'unid.']
    VALOR_KEYWORDS = [
        'valor', 'price', 'preço', 'preco', 'unitário', 'unitario', 'unit', 'v.unit',
        'preço unit', 'preco unit', 'p. unit', 'p.unit', 'valor unit',
    ]
    TOTAL_KEYWORDS = ['total', 'v.total', 'valor total', 'amount', 'preço total', 'preco total']
    CODIGO_KEYWORDS = ['código', 'codigo', 'code', 'ref', 'referência', 'referencia']
    ITEM_NUMERO_KEYWORDS = ['item', 'item n', 'nº item', 'n° item', 'nº', 'n°']
    BANCO_KEYWORDS = ['fonte', 'banco', 'base', 'origem', 'tabela']
    TOTAL_COM_BDI_KEYWORDS = ['c/ bdi', 'com bdi', 'c/bdi', 'c/ encargos', 'total c/']
    BDI_KEYWORDS = ['bdi %', '% bdi', 'bdi%', 'bdi (%)', '%bdi']
    BDI_STANDALONE = ('bdi', 'b.d.i', 'b.d.i.')
    PESO_KEYWORDS = ['peso', 'peso (%)', '% peso', 'particip', 'participação']
    
    # Palavras para ignorar (linhas de totalizações)
    IGNORE_KEYWORDS = [
        'total geral',
        'subtotal',
        'total:',
        'suma',
        'resumen',
        'grand total',
        'total do grupo',
    ]
    
    def __init__(self):
        self.confidence = 0.0
        self.structure = {}

    def _keyword_in_text(self, keyword: str, text: str) -> bool:
        keyword = keyword.strip().lower()
        if not keyword:
            return False
        if len(keyword) <= 3:
            return bool(re.search(rf"\b{re.escape(keyword)}\b", text))
        return keyword in text
    
    def parse_number(self, value: Any) -> float:
        """Converte string em número (formato BRL canônico)."""
        from app.domain.money import parse_brl

        return parse_brl(value)    
    def is_header_row(self, row: List[Any]) -> bool:
        """Verifica se a linha é um cabeçalho"""
        if not row:
            return False

        # Linha de item hierárquico nunca é cabeçalho
        first = str(row[0]).strip() if row else ""
        if self.looks_like_item_number(first):
            return False
        
        # Converte para texto minúsculo
        row_text = ' '.join(str(cell).lower() for cell in row if cell)
        
        # Conta quantas palavras-chave de cabeçalho aparecem
        keyword_count = 0
        all_keywords = (
            self.DESCRICAO_KEYWORDS
            + self.QUANTIDADE_KEYWORDS
            + self.UNIDADE_KEYWORDS
            + self.VALOR_KEYWORDS
            + self.CODIGO_KEYWORDS
            + self.ITEM_NUMERO_KEYWORDS
            + self.BANCO_KEYWORDS
            + self.BDI_KEYWORDS
        )

        for keyword in all_keywords:
            if self._keyword_in_text(keyword, row_text):
                keyword_count += 1

        has_codigo = any(self._keyword_in_text(k, row_text) for k in self.CODIGO_KEYWORDS)
        has_qtd = any(self._keyword_in_text(k, row_text) for k in self.QUANTIDADE_KEYWORDS)
        has_desc = any(self._keyword_in_text(k, row_text) for k in self.DESCRICAO_KEYWORDS)
        has_val = any(self._keyword_in_text(k, row_text) for k in self.VALOR_KEYWORDS)

        if has_codigo and (has_qtd or has_desc or has_val):
            return True
        if has_desc and (has_qtd or has_val):
            return True
        return keyword_count >= 3
    
    def should_ignore_row(self, row: List[Any]) -> bool:
        """Verifica se a linha deve ser ignorada"""
        if not row:
            return True
        
        row_text = ' '.join(str(cell).lower() for cell in row if cell).strip()
        
        # Ignorar linhas vazias
        if not row_text:
            return True
        
        # Ignorar totalizações
        for keyword in self.IGNORE_KEYWORDS:
            if keyword in row_text:
                return True
        
        # NÃO ignorar items com códigos hierárquicos (ex: "1.1", "1.1.1")
        # Estes são justamente os items principais do orçamento!
        
        return False

    def _default_structure(self) -> Dict[str, int]:
        return {
            'item_numero': -1,
            'banco': -1,
            'codigo': -1,
            'descricao': -1,
            'quantidade': -1,
            'unidade': -1,
            'valor_unitario': -1,
            'valor_total': -1,
            'valor_total_sem_bdi': -1,
            'valor_total_com_bdi': -1,
            'bdi': -1,
        }

    def looks_like_item_number(self, value: Any) -> bool:
        """Aceita 1, 1.1, 1.1.2… (regex do pipeline por coordenadas)."""
        text = str(value or "").strip()
        if not text:
            return False
        if not re.match(r"^\d+(?:\.\d+)*$", text):
            return False
        # Evita telefone/CEP soltos no rodapé (ex.: "92")
        if text.isdigit() and int(text) > 40:
            return False
        return True

    def _cell_text(self, row: List[Any], index: int) -> str:
        if index < 0 or index >= len(row):
            return ""
        return str(row[index]).strip()

    def _normalize_bdi_percent(
        self,
        bdi: float,
        quantidade: float,
        valor_unitario: float,
        valor_total: float,
        valor_total_sem_bdi: float = 0.0,
    ) -> float:
        if 0 < bdi <= 100:
            return bdi
        if valor_total_sem_bdi > 0 and valor_total > valor_total_sem_bdi:
            inferred = (valor_total / valor_total_sem_bdi - 1) * 100
            if 0 < inferred <= 100:
                return round(inferred, 2)
        if quantidade > 0 and valor_unitario > 0 and valor_total > 0:
            base = quantidade * valor_unitario
            if valor_total > base * 1.001:
                inferred = (valor_total / base - 1) * 100
                if 0 < inferred <= 100:
                    return round(inferred, 2)
        return 0.0

    def _find_item_column(self, row: List[Any]) -> int:
        for idx, cell in enumerate(row):
            if self.looks_like_item_number(cell):
                return idx
        return -1

    def try_parse_sintetico_row(self, row: List[Any]) -> Optional[Dict[str, Any]]:
        """
        Orçamento Sintético (NOVACAP/edital):
        - Executivo: Item | Código | Banco | Desc | Und | Qtd | VU c/BDI | Total | Peso%
        - Grupo: Item | Desc | Qtd | VU | Total | Peso%  (colunas esparsas)
        """
        cells = [self._cell_text(row, i) for i in range(len(row)) if self._cell_text(row, i)]
        if not cells or not self.looks_like_item_number(cells[0]):
            return None

        item_numero = cells[0]
        depth = item_numero.count(".")
        # Layout executivo completo: Item|Código|Banco|Desc|Und|Qtd|VU|Total|Peso
        is_executive_layout = len(cells) >= 7 and depth >= 1

        money_idxs: list[int] = []
        for i, c in enumerate(cells):
            if i == 0:
                continue
            # Ignora peso% e textos com % no meio da descrição
            if "%" in c and len(c) < 16:
                continue
            # "4.083,30 (BDI 15,28%)" ou dinheiro puro
            if re.match(r"^[\d.]+,\d{2}(?:\s*\(.*\))?$", c.replace("R$", "").strip()) or (
                re.match(r"^[\d.]+,\d{2}", c) and i >= len(cells) - 4
            ):
                money_idxs.append(i)
                continue
            if re.match(r"^\d{1,3}(?:\.\d{3})+,\d{2}", c):
                money_idxs.append(i)

        if not money_idxs:
            return None

        def _money_at(i: int) -> float:
            c = cells[i]
            m = re.match(r"^([\d.]+,\d{2})", c.replace("R$", "").strip())
            return self.parse_number(m.group(1) if m else c)

        valor_total = _money_at(money_idxs[-1])
        valor_unitario = _money_at(money_idxs[-2]) if len(money_idxs) >= 2 else valor_total

        # qty: célula numérica antes do VU
        vu_i = money_idxs[-2] if len(money_idxs) >= 2 else money_idxs[-1]
        quantidade = 0.0
        for j in range(vu_i - 1, 0, -1):
            c = cells[j]
            if "%" in c:
                continue
            if re.match(r"^[\d.,]+$", c):
                quantidade = self.parse_number(c)
                break

        if depth <= 1 and not is_executive_layout and quantidade <= 0:
            quantidade = 1.0

        codigo = ""
        banco = ""
        unidade = "un"
        descricao = ""

        if is_executive_layout:
            codigo = cells[1]
            banco = cells[2]
            for j in range(vu_i - 1, 2, -1):
                c = cells[j]
                if re.match(r"^[\d.,]+$", c):
                    continue
                if len(c) <= 6:
                    unidade = c
                    descricao = " ".join(cells[3:j])
                    break
            if not descricao:
                descricao = cells[3]
        else:
            descricao = cells[1] if len(cells) > 1 else ""
            if depth <= 1:
                codigo = ""
                banco = ""
            if quantidade <= 0:
                quantidade = 1.0

        if not descricao or len(descricao) < 3:
            return None
        if valor_total <= 0 and quantidade <= 0 and valor_unitario <= 0:
            return None

        return {
            "item_numero": item_numero,
            "item": item_numero,
            "banco": banco,
            "codigo": codigo,
            "descricao": descricao,
            "quantidade": quantidade,
            "unidade": unidade,
            "bdi": 0.0,
            "valor_unitario": valor_unitario,
            "valor_total": valor_total,
        }

    def _looks_like_sintetico_continuation(self, rows: List[List[Any]]) -> bool:
        xyz = 0
        peso = 0
        for row in rows[:25]:
            cells = [self._cell_text(row, i) for i in range(len(row)) if self._cell_text(row, i)]
            if not cells:
                continue
            if self.looks_like_item_number(cells[0]) and cells[0].count(".") >= 2:
                xyz += 1
            if any("%" in c and len(c) < 16 for c in cells[-3:]):
                peso += 1
        return xyz >= 5 and peso >= 5

    def _sintetico_header(self) -> List[str]:
        return [
            "Item",
            "Código",
            "Banco",
            "Descrição",
            "Und",
            "Quant.",
            "Valor Unit com BDI",
            "Total",
            "Peso (%)",
        ]

    def try_parse_novacap_row(self, row: List[Any]) -> Optional[Dict[str, Any]]:
        """Layout NOVACAP: Item | Fonte | Código | Descrição | Unid | Qtde | V.Unit | Total | BDI | Total c/ BDI."""
        item_col = self._find_item_column(row)
        if item_col < 0:
            return None

        tail = [self._cell_text(row, i) for i in range(item_col, len(row))]
        while tail and not tail[-1]:
            tail.pop()
        if len(tail) < 8:
            return None

        item_numero = tail[0]
        banco = tail[1] if len(tail) > 1 else ""
        codigo = tail[2] if len(tail) > 2 else ""
        descricao = tail[3] if len(tail) > 3 else ""
        unidade = (tail[4] if len(tail) > 4 else "") or "un"

        quantidade = 0.0
        valor_unitario = 0.0
        valor_total_sem_bdi = 0.0
        bdi_raw = 0.0
        valor_total = 0.0

        if len(tail) >= 10:
            quantidade = self.parse_number(tail[5])
            valor_unitario = self.parse_number(tail[6])
            valor_total_sem_bdi = self.parse_number(tail[7])
            bdi_raw = self.parse_number(tail[8])
            valor_total = self.parse_number(tail[9])
        else:
            nums = [self.parse_number(v) for v in tail[5:]]
            if len(nums) >= 1:
                quantidade = nums[0]
            if len(nums) >= 2:
                valor_unitario = nums[1]
            if len(nums) >= 3:
                valor_total_sem_bdi = nums[2]
            if len(nums) >= 4:
                bdi_raw = nums[3]
            if len(nums) >= 5:
                valor_total = nums[4]
            elif nums:
                valor_total = nums[-1]

        bdi = self._normalize_bdi_percent(
            bdi_raw, quantidade, valor_unitario, valor_total, valor_total_sem_bdi
        )
        if valor_total <= 0 and valor_total_sem_bdi > 0:
            valor_total = (
                valor_total_sem_bdi * (1 + bdi / 100) if bdi > 0 else valor_total_sem_bdi
            )
        if valor_total <= 0 and quantidade > 0 and valor_unitario > 0:
            factor = 1 + bdi / 100 if bdi > 0 else 1
            valor_total = quantidade * valor_unitario * factor

        if not descricao or len(descricao) < 3:
            return None
        if quantidade <= 0 and valor_unitario <= 0 and valor_total <= 0:
            return None

        return {
            "item_numero": item_numero,
            "item": item_numero,
            "banco": banco,
            "codigo": codigo,
            "descricao": descricao,
            "quantidade": quantidade,
            "unidade": unidade,
            "bdi": bdi,
            "valor_unitario": valor_unitario,
            "valor_total": valor_total,
        }

    def try_parse_loose_text_row(self, row: List[Any]) -> Optional[Dict[str, Any]]:
        """Fallback para linhas em que o pdfplumber fundiu colunas em poucas células."""
        cells = [self._cell_text(row, i) for i in range(len(row)) if self._cell_text(row, i)]
        if not cells:
            return None
        joined = " ".join(cells).strip()
        if len(joined) < 20 or not re.search(r"\d+\.\d+\.\d+", joined):
            return None

        sintetico = self.try_parse_sintetico_row(row)
        if sintetico:
            return sintetico

        novacap = self.try_parse_novacap_row(row)
        if novacap:
            return novacap

        tokens = joined.split()
        if len(tokens) < 9 or not self.looks_like_item_number(tokens[0]):
            return None

        numeric_idx: list[int] = []
        for idx in range(len(tokens) - 1, 0, -1):
            token = tokens[idx].replace("R$", "")
            if not token:
                continue
            parsed = self.parse_number(token)
            if parsed > 0 or token in {"0", "0,00", "0.00"} or "%" in token:
                numeric_idx.insert(0, idx)
            if len(numeric_idx) >= 5:
                break

        if len(numeric_idx) < 3:
            return None

        nums = [self.parse_number(tokens[i].replace("%", "")) for i in numeric_idx[-5:]]
        while len(nums) < 5:
            nums.insert(0, 0.0)
        quantidade, valor_unitario, valor_total_sem_bdi, bdi_raw, valor_total = nums[-5:]

        unit_idx = numeric_idx[0] - 1
        unidade = tokens[unit_idx] if unit_idx >= 1 else "un"
        item_numero = tokens[0]
        banco = tokens[1] if len(tokens) > 1 else ""
        codigo = tokens[2] if len(tokens) > 2 else ""
        descricao = " ".join(tokens[3:unit_idx]) if unit_idx > 3 else ""

        bdi = self._normalize_bdi_percent(
            bdi_raw, quantidade, valor_unitario, valor_total, valor_total_sem_bdi
        )
        if valor_total <= 0 and valor_total_sem_bdi > 0:
            valor_total = (
                valor_total_sem_bdi * (1 + bdi / 100) if bdi > 0 else valor_total_sem_bdi
            )
        if valor_total <= 0 and quantidade > 0 and valor_unitario > 0:
            factor = 1 + bdi / 100 if bdi > 0 else 1
            valor_total = quantidade * valor_unitario * factor

        if not descricao or len(descricao) < 3:
            return None
        if quantidade <= 0 and valor_unitario <= 0 and valor_total <= 0:
            return None

        return {
            "item_numero": item_numero,
            "item": item_numero,
            "banco": banco,
            "codigo": codigo,
            "descricao": descricao,
            "quantidade": quantidade,
            "unidade": unidade,
            "bdi": bdi,
            "valor_unitario": valor_unitario,
            "valor_total": valor_total,
        }

    def parse_table_row_scan(self, rows: List[List[Any]], page: int = 0) -> List[Dict[str, Any]]:
        """Varredura linha a linha (NOVACAP) sem depender de cabeçalho ou colunas fixas."""
        items: List[Dict[str, Any]] = []
        for idx, row in enumerate(rows):
            if not row or self.is_header_row(row) or self.should_ignore_row(row):
                continue
            parsed = (
                self.try_parse_sintetico_row(row)
                or self.try_parse_novacap_row(row)
                or self.try_parse_loose_text_row(row)
            )
            if not parsed:
                continue
            items.append(
                {
                    "id": f"item_{page}_{idx}",
                    "item_numero": parsed.get("item_numero"),
                    "item": parsed.get("item_numero"),
                    "banco": parsed.get("banco"),
                    "codigo": parsed.get("codigo"),
                    "descricao": parsed.get("descricao"),
                    "quantidade": parsed.get("quantidade"),
                    "unidade": parsed.get("unidade"),
                    "bdi": parsed.get("bdi"),
                    "valor_unitario": parsed.get("valor_unitario"),
                    "valor_total": parsed.get("valor_total"),
                    "status": "validado",
                    "origem": f"página {page}, linha {idx}",
                }
            )
        logger.info("Varredura NOVACAP: %s itens na página %s", len(items), page)
        return items

    def _merge_row_fields(
        self,
        primary: Dict[str, Any],
        fallback: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not fallback:
            return primary
        merged = dict(primary)
        for key in (
            "item_numero",
            "item",
            "banco",
            "codigo",
            "descricao",
            "unidade",
            "quantidade",
            "valor_unitario",
            "valor_total",
            "bdi",
        ):
            pv = merged.get(key)
            fv = fallback.get(key)
            if key in {"quantidade", "valor_unitario", "bdi"}:
                if (not pv or float(pv or 0) <= 0) and fv and float(fv or 0) > 0:
                    merged[key] = fv
            elif key == "valor_total":
                pv_num = float(pv or 0)
                fv_num = float(fv or 0)
                if fv_num > pv_num:
                    merged[key] = fv
                elif pv_num > 0:
                    merged[key] = pv_num
            elif key == "codigo":
                # Não deixar descrição longa no campo código
                pv_s = str(pv or "").strip()
                fv_s = str(fv or "").strip()
                if (not pv_s or len(pv_s) > 48 or " " in pv_s and len(pv_s) > 20) and fv_s:
                    merged[key] = fv_s
                elif not pv_s and fv_s:
                    merged[key] = fv_s
            elif (not pv or str(pv).strip() == "") and fv:
                merged[key] = fv
        return merged
    
    def _resolve_valor_total(
        self,
        structure: Dict[str, int],
        row: List[Any],
        quantidade: float,
        valor_unitario: float,
        bdi: float,
    ) -> float:
        total_com = (
            self.parse_number(row[structure['valor_total_com_bdi']])
            if structure.get('valor_total_com_bdi', -1) >= 0
            and structure['valor_total_com_bdi'] < len(row)
            else 0.0
        )
        if total_com > 0:
            return total_com

        total_sem = (
            self.parse_number(row[structure['valor_total_sem_bdi']])
            if structure.get('valor_total_sem_bdi', -1) >= 0
            and structure['valor_total_sem_bdi'] < len(row)
            else 0.0
        )
        if total_sem <= 0 and structure.get('valor_total', -1) >= 0 and structure['valor_total'] < len(row):
            total_sem = self.parse_number(row[structure['valor_total']])

        if total_sem > 0:
            if bdi > 0 and structure.get('valor_total_com_bdi', -1) < 0:
                return total_sem * (1 + bdi / 100)
            return total_sem

        if quantidade > 0 and valor_unitario > 0:
            factor = 1 + bdi / 100 if bdi > 0 else 1
            return quantidade * valor_unitario * factor
        return 0.0
    
    def identify_columns(self, header_row: List[Any]) -> Dict[str, int]:
        """Identifica os índices das colunas importantes"""
        structure = self._default_structure()

        qty_plain = -1
        qty_max = -1
        qty_min = -1

        for idx, cell in enumerate(header_row):
            cell_lower = str(cell).lower().strip()

            if structure['item_numero'] == -1:
                if cell_lower in ('item', 'item.', 'nº item', 'n° item'):
                    structure['item_numero'] = idx
                elif cell_lower.startswith('item ') and 'código' not in cell_lower and 'codigo' not in cell_lower:
                    structure['item_numero'] = idx
                else:
                    for keyword in self.ITEM_NUMERO_KEYWORDS:
                        if keyword == cell_lower or cell_lower.startswith(f"{keyword} "):
                            structure['item_numero'] = idx
                            break

            if structure['banco'] == -1:
                for keyword in self.BANCO_KEYWORDS:
                    if keyword in cell_lower:
                        structure['banco'] = idx
                        break

            if structure['codigo'] == -1:
                for keyword in self.CODIGO_KEYWORDS:
                    if keyword in cell_lower and 'item' not in cell_lower:
                        structure['codigo'] = idx
                        break

            if structure['bdi'] == -1:
                # Nunca confundir "Valor Unit com BDI" / "Total c/ BDI" com coluna de BDI%
                looks_like_price = any(
                    token in cell_lower
                    for token in ("valor", "preço", "preco", "unit", "total", "price")
                )
                if not looks_like_price:
                    if cell_lower.strip() in self.BDI_STANDALONE:
                        structure['bdi'] = idx
                    else:
                        for keyword in self.BDI_KEYWORDS:
                            if keyword in cell_lower:
                                structure['bdi'] = idx
                                break

            if structure['descricao'] == -1:
                for keyword in self.DESCRICAO_KEYWORDS:
                    if keyword in cell_lower:
                        structure['descricao'] = idx
                        break

            if 'qtde' in cell_lower or 'quant' in cell_lower or 'qtd' in cell_lower:
                if 'mín' in cell_lower or 'min' in cell_lower:
                    qty_min = idx
                elif 'máx' in cell_lower or 'max' in cell_lower:
                    qty_max = idx
                elif qty_plain < 0:
                    qty_plain = idx

            if structure['unidade'] == -1:
                for keyword in self.UNIDADE_KEYWORDS:
                    if keyword in cell_lower:
                        structure['unidade'] = idx
                        break

            if structure['valor_unitario'] == -1:
                for keyword in self.VALOR_KEYWORDS:
                    if keyword in cell_lower and 'total' not in cell_lower:
                        structure['valor_unitario'] = idx
                        break

            if 'total' in cell_lower and 'peso' not in cell_lower:
                is_com_bdi = any(kw in cell_lower for kw in self.TOTAL_COM_BDI_KEYWORDS)
                if is_com_bdi or ('bdi' in cell_lower and ('c/' in cell_lower or 'com ' in cell_lower)):
                    structure['valor_total_com_bdi'] = idx
                elif structure['valor_total_sem_bdi'] == -1:
                    structure['valor_total_sem_bdi'] = idx

            # "Valor Unit com BDI" é preço unitário, não total
            if structure['valor_unitario'] == -1 and 'valor unit' in cell_lower and 'total' not in cell_lower:
                structure['valor_unitario'] = idx

        if qty_plain >= 0:
            structure['quantidade'] = qty_plain
        elif qty_max >= 0:
            structure['quantidade'] = qty_max
        elif qty_min >= 0:
            structure['quantidade'] = qty_min

        if structure['valor_total_com_bdi'] >= 0:
            structure['valor_total'] = structure['valor_total_com_bdi']
        elif structure['valor_total_sem_bdi'] >= 0:
            structure['valor_total'] = structure['valor_total_sem_bdi']

        return structure
    
    def guess_columns_from_data(self, rows: List[List[Any]]) -> Dict[str, int]:
        """Tenta adivinhar colunas analisando os dados (fallback)"""
        if not rows or len(rows) < 2:
            return {}
        
        structure = self._default_structure()
        num_cols = max(len(row) for row in rows)

        item_col_matches = 0
        for row in rows[1:20]:
            if row and self.looks_like_item_number(row[0]):
                item_col_matches += 1

        if item_col_matches >= 3 and num_cols >= 8:
            structure['item_numero'] = 0
            structure['banco'] = 1
            structure['codigo'] = 2
            structure['descricao'] = 3
            structure['unidade'] = 4
            structure['quantidade'] = 5
            structure['valor_unitario'] = 6
            structure['valor_total_sem_bdi'] = 7
            if num_cols >= 9:
                structure['bdi'] = 8
            if num_cols >= 10:
                structure['valor_total_com_bdi'] = 9
            if structure['valor_total_com_bdi'] >= 0:
                structure['valor_total'] = structure['valor_total_com_bdi']
            elif structure['valor_total_sem_bdi'] >= 0:
                structure['valor_total'] = structure['valor_total_sem_bdi']
            return structure
        
        # Heurística genérica: descrição geralmente é a coluna mais larga com texto
        text_lengths = [0] * num_cols
        numeric_counts = [0] * num_cols
        
        for row in rows[:10]:
            for idx, cell in enumerate(row):
                if idx < num_cols:
                    cell_str = str(cell).strip()
                    text_lengths[idx] += len(cell_str)
                    if self.parse_number(cell) > 0:
                        numeric_counts[idx] += 1
        
        if text_lengths:
            structure['descricao'] = text_lengths.index(max(text_lengths))
        
        numeric_cols = [i for i, count in enumerate(numeric_counts) if count > len(rows) * 0.3]
        
        if numeric_cols:
            if len(numeric_cols) >= 1:
                structure['quantidade'] = numeric_cols[0]
            if len(numeric_cols) >= 2:
                structure['valor_unitario'] = numeric_cols[-2]
            if len(numeric_cols) >= 3:
                structure['valor_total_sem_bdi'] = numeric_cols[-1]
                structure['valor_total'] = numeric_cols[-1]
        
        if structure['quantidade'] != -1:
            structure['unidade'] = structure['quantidade'] + 1
        
        return structure
    
    def parse_table(self, rows: List[List[Any]], page: int = 0) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """
        Parseia uma tabela de orçamento
        
        Returns:
            (items, structure): Lista de itens extraídos e estrutura detectada
        """
        items = []
        structure = {}
        
        if not rows or len(rows) < 2:
            return items, structure
        
        # 1. Tentar identificar cabeçalho - procurar nas primeiras linhas
        # Prefere cabeçalho real (Item|Código|Descrição|Total) a metadados ("Obra Bancos B.D.I.")
        header_idx = -1
        best_header_score = -1
        for idx, row in enumerate(rows[:25]):
            if not self.is_header_row(row):
                continue
            candidate = self.identify_columns(row)
            score = sum(
                1
                for key in (
                    "item_numero",
                    "codigo",
                    "descricao",
                    "quantidade",
                    "valor_unitario",
                    "valor_total",
                    "valor_total_sem_bdi",
                    "valor_total_com_bdi",
                )
                if candidate.get(key, -1) >= 0
            )
            row_l = " ".join(str(c).lower() for c in row)
            if "descri" in row_l:
                score += 2
            if "item" in row_l and ("código" in row_l or "codigo" in row_l):
                score += 2
            if "obra bancos" in row_l or "encargos sociais" in row_l:
                score -= 5
            if score > best_header_score and (
                candidate.get("descricao", -1) != -1 or candidate.get("codigo", -1) != -1
            ):
                best_header_score = score
                header_idx = idx
                structure = candidate
        # Cabeçalho fraco (ex.: linha de dados com "descrição" longa) não conta
        if header_idx >= 0 and best_header_score < 5:
            logger.info(
                "📋 Cabeçalho fraco ignorado (score=%s) na linha %s",
                best_header_score,
                header_idx,
            )
            header_idx = -1
            structure = {}
        if header_idx >= 0:
            logger.info(f"📋 Cabeçalho detectado na linha {header_idx}: {structure}")

        # Continuação de Orçamento Sintético sem cabeçalho na página
        if header_idx < 0 and self._looks_like_sintetico_continuation(rows):
            synthetic_header = self._sintetico_header()
            rows = [synthetic_header] + list(rows)
            structure = self.identify_columns(synthetic_header)
            header_idx = 0
            logger.info("📋 Cabeçalho sintético injetado (continuação de página)")
        
        # 2. Se não encontrou cabeçalho, tenta adivinhar
        if header_idx == -1:
            logger.warning("⚠️ Cabeçalho não encontrado, tentando adivinhar estrutura...")
            structure = self.guess_columns_from_data(rows)
            header_idx = 0
        
        # 3. Verificar se estrutura é válida
        if (
            structure.get('descricao', -1) == -1
            and structure.get('codigo', -1) == -1
            and structure.get('item_numero', -1) == -1
        ):
            logger.warning("⚠️ Não foi possível identificar colunas de descrição/código")
            return items, structure
        
        # 4. Extrair itens (suporta cabeçalhos repetidos entre grupos)
        active_structure = dict(structure)
        for idx, row in enumerate(rows[header_idx + 1:], start=header_idx + 1):
            if self.is_header_row(row):
                active_structure = self.identify_columns(row)
                continue
            if self.should_ignore_row(row):
                continue

            try:
                item_numero = ""
                if active_structure.get('item_numero', -1) >= 0:
                    item_numero = self._cell_text(row, active_structure['item_numero'])
                if not item_numero and row and self.looks_like_item_number(row[0]):
                    item_numero = self._cell_text(row, 0)

                banco = ""
                if active_structure.get('banco', -1) >= 0:
                    banco = self._cell_text(row, active_structure['banco'])

                codigo = ""
                if active_structure.get('codigo', -1) >= 0:
                    codigo = self._cell_text(row, active_structure['codigo'])

                if active_structure.get('descricao', -1) >= 0:
                    descricao = self._cell_text(row, active_structure['descricao'])
                else:
                    descricao = ""
                if not descricao and codigo:
                    descricao = codigo

                quantidade = (
                    self.parse_number(row[active_structure['quantidade']])
                    if active_structure.get('quantidade', -1) >= 0 and active_structure['quantidade'] < len(row)
                    else 0
                )
                unidade = (
                    self._cell_text(row, active_structure['unidade'])
                    if active_structure.get('unidade', -1) >= 0
                    else "un"
                ) or "un"
                bdi_raw = (
                    self.parse_number(row[active_structure['bdi']])
                    if active_structure.get('bdi', -1) >= 0 and active_structure['bdi'] < len(row)
                    else 0.0
                )
                # BDI% inválido (>100) nunca entra no cálculo de total
                bdi_for_total = bdi_raw if 0 < bdi_raw <= 100 else 0.0
                valor_total_sem_bdi = (
                    self.parse_number(row[active_structure['valor_total_sem_bdi']])
                    if active_structure.get('valor_total_sem_bdi', -1) >= 0
                    and active_structure['valor_total_sem_bdi'] < len(row)
                    else 0.0
                )
                valor_unitario = (
                    self.parse_number(row[active_structure['valor_unitario']])
                    if active_structure.get('valor_unitario', -1) >= 0 and active_structure['valor_unitario'] < len(row)
                    else 0
                )

                valor_total = self._resolve_valor_total(
                    active_structure, row, quantidade, valor_unitario, bdi_for_total
                )
                bdi = self._normalize_bdi_percent(
                    bdi_raw, quantidade, valor_unitario, valor_total, valor_total_sem_bdi
                )

                if bdi > 0 and valor_unitario > 0 and abs(valor_unitario - bdi) < 0.01:
                    # Coluna BDI colidiu com VU — descartar BDI falso
                    bdi = 0.0

                novacap = (
                    self.try_parse_sintetico_row(row)
                    or self.try_parse_novacap_row(row)
                    or self.try_parse_loose_text_row(row)
                )
                if novacap:
                    # Sintético/NOVACAP é fonte preferencial (evita código=descrição em grupos)
                    merged = self._merge_row_fields(
                        novacap,
                        {
                            "item_numero": item_numero,
                            "item": item_numero,
                            "banco": banco,
                            "codigo": codigo,
                            "descricao": descricao,
                            "quantidade": quantidade,
                            "unidade": unidade,
                            "bdi": bdi,
                            "valor_unitario": valor_unitario,
                            "valor_total": valor_total,
                        },
                    )
                    item_numero = str(merged.get("item_numero") or "")
                    banco = str(merged.get("banco") or "")
                    codigo = str(merged.get("codigo") or "")
                    descricao = str(merged.get("descricao") or "")
                    quantidade = float(merged.get("quantidade") or 0)
                    unidade = str(merged.get("unidade") or "un")
                    bdi = float(merged.get("bdi") or 0)
                    valor_unitario = float(merged.get("valor_unitario") or 0)
                    valor_total = float(merged.get("valor_total") or 0)

                if not descricao or len(descricao) < 3:
                    continue

                if quantidade <= 0 and valor_unitario <= 0 and valor_total <= 0:
                    continue

                items.append({
                    'id': f'item_{page}_{idx}',
                    'item_numero': item_numero,
                    'item': item_numero,
                    'banco': banco,
                    'codigo': codigo,
                    'descricao': descricao,
                    'quantidade': quantidade,
                    'unidade': unidade,
                    'bdi': bdi,
                    'valor_unitario': valor_unitario,
                    'valor_total': valor_total if valor_total > 0 else quantidade * valor_unitario,
                    'status': 'validado',
                    'origem': f'página {page}, linha {idx}'
                })
            
            except (IndexError, ValueError, TypeError) as e:
                logger.debug(f"Erro ao processar linha {idx}: {e}")
                continue
        
        logger.info(f"✅ Extraídos {len(items)} itens da página {page}")
        return items, structure
    
    def parse_all_tables(self, tables: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Parseia todas as tabelas extraídas do PDF
        
        Args:
            tables: Lista de dicionários com 'page', 'rows', etc
        
        Returns:
            Dicionário com items, resumo e estrutura
        """
        all_items = []
        structures = []
        
        # Detectar tabelas de orçamento sintético vs. composições detalhadas
        priority_tables = []
        other_tables = []
        
        for table in tables:
            rows = table.get('rows', [])
            # Verificar se tem "orçamento sintético" nas primeiras linhas
            has_orcamento_sintetico = False
            for row in rows[:3]:
                row_text = ' '.join(str(cell).lower() for cell in row if cell)
                if 'orçamento sintético' in row_text or 'orcamento sintetico' in row_text:
                    has_orcamento_sintetico = True
                    break
            
            if has_orcamento_sintetico:
                priority_tables.append(table)
                logger.info(f"📊 Tabela prioritária detectada (Orçamento Sintético) na página {table.get('page', 0)}")
            else:
                other_tables.append(table)
        
        # Processar tabelas prioritárias primeiro
        tables_to_process = priority_tables if priority_tables else other_tables
        
        for table in tables_to_process:
            page = table.get('page', 0)
            rows = table.get('rows', [])
            
            items, structure = self.parse_table(rows, page)
            all_items.extend(items)
            if structure:
                structures.append(structure)
        
        # Filtrar items de baixo valor (provavelmente composições internas)
        # Items principais geralmente têm valor total > R$ 10
        MIN_VALUE_THRESHOLD = 10.0
        
        main_items = [item for item in all_items if item['valor_total'] >= MIN_VALUE_THRESHOLD]
        low_value_items = [item for item in all_items if item['valor_total'] < MIN_VALUE_THRESHOLD]
        
        # Se temos items principais, usar apenas eles. Senão, usar todos.
        final_items = main_items if main_items else all_items
        
        logger.info(f"📊 Total de items extraídos: {len(all_items)}")
        logger.info(f"📊 Items principais (≥ R$ {MIN_VALUE_THRESHOLD}): {len(main_items)}")
        logger.info(f"📊 Items de baixo valor: {len(low_value_items)}")
        
        # Calcular resumo
        total_value = sum(item['valor_total'] for item in final_items)
        
        return {
            'status': 'success',
            'items': final_items,
            'resumo': {
                'total_items': len(final_items),
                'valor_total': total_value,
                'confianca': 0.85 if structures else 0.5,
                'metodo': 'parser_deterministico'
            },
            'estruturas_detectadas': structures
        }

