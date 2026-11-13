import pytest


@pytest.fixture
def raw_theme():
    # Synthetic schema fixture only. Never packaged or seeded in production.
    return dict(
        ID="T1",
        Name="DEMO 大题材",
        ClassLayer="3",
        errcode="0",
        BriefIntro="DEMO",
        Table=[
            dict(
                Level1=dict(
                    ID="L1",
                    Name="供应商",
                    Stocks=[dict(StockID="600519", prod_name="DEMO甲", IsZz="2")],
                ),
                Level2=[
                    dict(
                        ID="L2",
                        Name="整机",
                        Stocks=[dict(StockID="300863", prod_name="DEMO乙")],
                    )
                ],
            )
        ],
        StockList=[
            dict(
                StockID="300863", prod_name="DEMO乙", Tag=[dict(ID="L2", Name="整机")]
            ),
            dict(
                StockID="000001",
                prod_name="DEMO丙",
                Tag=[dict(ID="unmatched", Name="不猜测")],
            ),
        ],
        Stocks=[dict(StockID="600519.SH", prod_name="DEMO甲")],
    )
