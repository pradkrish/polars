# flake8: noqa: W191,E101
import copy
import gzip
import io
import pickle
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import polars as pl


def test_to_from_buffer(df):
    df = df.drop("strings_nulls")

    for to_fn, from_fn in zip(
        [df.to_parquet, df.to_csv, df.to_ipc, df.to_json],
        [pl.read_parquet, pl.read_csv, pl.read_ipc, pl.read_json],
    ):
        f = io.BytesIO()
        to_fn(f)
        f.seek(0)

        df_1 = from_fn(f)
        assert df.frame_equal(df_1, null_equal=True)


def test_select_columns_from_buffer():
    df = pl.DataFrame({"a": [1, 2, 3], "b": [True, False, True], "c": ["a", "b", "c"]})
    expected = pl.DataFrame({"b": [True, False, True], "c": ["a", "b", "c"]})
    for to_fn, from_fn in zip(
        [df.to_parquet, df.to_ipc], [pl.read_parquet, pl.read_ipc]
    ):
        f = io.BytesIO()
        to_fn(f)
        f.seek(0)
        df_1 = from_fn(f, columns=["b", "c"], use_pyarrow=False)
        assert df_1.frame_equal(expected)


def test_read_web_file():
    url = "https://raw.githubusercontent.com/pola-rs/polars/master/examples/aggregate_multiple_files_in_chunks/datasets/foods1.csv"
    df = pl.read_csv(url)
    assert df.shape == (27, 4)


def test_parquet_chunks():
    """
    This failed in https://github.com/pola-rs/polars/issues/545
    """
    cases = [
        1048576,
        1048577,
    ]

    for case in cases:
        f = io.BytesIO()
        # repeat until it has case instances
        df = pd.DataFrame(
            np.tile([1.0, pd.to_datetime("2010-10-10")], [case, 1]),
            columns=["floats", "dates"],
        )
        print(df)

        # write as parquet
        df.to_parquet(f)

        print(f"reading {case} dates with polars...", end="")
        f.seek(0)

        # read it with polars
        polars_df = pl.read_parquet(f)
        assert pl.DataFrame(df).frame_equal(polars_df)


def test_parquet_datetime():
    """
    This failed because parquet writers cast datetimeto Date
    """
    f = io.BytesIO()
    data = {
        "datetime": [  # unix timestamp in ms
            1618354800000,
            1618354740000,
            1618354680000,
            1618354620000,
            1618354560000,
        ],
        "laf_max": [73.1999969482, 71.0999984741, 74.5, 69.5999984741, 69.6999969482],
        "laf_eq": [59.5999984741, 61.0, 62.2999992371, 56.9000015259, 60.0],
    }
    df = pl.DataFrame(data)
    df = df.with_column(df["datetime"].cast(pl.Datetime))

    df.to_parquet(f, use_pyarrow=True)
    f.seek(0)
    read = pl.read_parquet(f)
    assert read.frame_equal(df)


def test_csv_null_values():
    csv = """
a,b,c
na,b,c
a,na,c"""
    f = io.StringIO(csv)

    df = pl.read_csv(f, null_values="na")
    assert df[0, "a"] is None
    assert df[1, "b"] is None

    csv = """
a,b,c
na,b,c
a,n/a,c"""
    f = io.StringIO(csv)
    df = pl.read_csv(f, null_values=["na", "n/a"])
    assert df[0, "a"] is None
    assert df[1, "b"] is None

    csv = """
a,b,c
na,b,c
a,n/a,c"""
    f = io.StringIO(csv)
    df = pl.read_csv(f, null_values={"a": "na", "b": "n/a"})
    assert df[0, "a"] is None
    assert df[1, "b"] is None


def test_datetime_parsing():
    csv = """
timestamp,open,high
2021-01-01 00:00:00,0.00305500,0.00306000
2021-01-01 00:15:00,0.00298800,0.00300400
2021-01-01 00:30:00,0.00298300,0.00300100
2021-01-01 00:45:00,0.00299400,0.00304000
"""

    f = io.StringIO(csv)
    df = pl.read_csv(f)
    assert df.dtypes == [pl.Datetime, pl.Float64, pl.Float64]


def test_partial_dtype_overwrite():
    csv = """
a,b,c
1,2,3
1,2,3
"""
    f = io.StringIO(csv)
    df = pl.read_csv(f, dtype=[pl.Utf8])
    assert df.dtypes == [pl.Utf8, pl.Int64, pl.Int64]


def test_partial_column_rename():
    csv = """
a,b,c
1,2,3
1,2,3
"""
    f = io.StringIO(csv)
    for use in [True, False]:
        f.seek(0)
        df = pl.read_csv(f, new_columns=["foo"], use_pyarrow=use)
        assert df.columns == ["foo", "b", "c"]


def test_column_rename_and_dtype_overwrite():
    csv = """
a,b,c
1,2,3
1,2,3
"""
    f = io.StringIO(csv)
    df = pl.read_csv(
        f,
        new_columns=["A", "B", "C"],
        dtype={"A": pl.Utf8, "B": pl.Int64, "C": pl.Float32},
    )
    assert df.dtypes == [pl.Utf8, pl.Int64, pl.Float32]

    f = io.StringIO(csv)
    df = pl.read_csv(
        f,
        columns=["a", "c"],
        new_columns=["A", "C"],
        dtype={"A": pl.Utf8, "C": pl.Float32},
    )
    assert df.dtypes == [pl.Utf8, pl.Float32]

    csv = """
1,2,3
1,2,3
"""
    f = io.StringIO(csv)
    df = pl.read_csv(
        f,
        new_columns=["A", "B", "C"],
        dtype={"A": pl.Utf8, "C": pl.Float32},
        has_headers=False,
    )
    assert df.dtypes == [pl.Utf8, pl.Int64, pl.Float32]


def test_compressed_csv():
    # gzip compression
    csv = """
a,b,c
1,a,1.0
2,b,2.0,
3,c,3.0
"""
    fout = io.BytesIO()
    with gzip.GzipFile(fileobj=fout, mode="w") as f:
        f.write(csv.encode())

    csv_bytes = fout.getvalue()
    out = pl.read_csv(csv_bytes)
    expected = pl.DataFrame(
        {"a": [1, 2, 3], "b": ["a", "b", "c"], "c": [1.0, 2.0, 3.0]}
    )
    assert out.frame_equal(expected)

    # now from disk
    out = pl.read_csv("tests/files/gzipped.csv")
    assert out.frame_equal(expected)

    # now with column projection
    out = pl.read_csv(csv_bytes, columns=["a", "b"])
    expected = pl.DataFrame({"a": [1, 2, 3], "b": ["a", "b", "c"]})
    assert out.frame_equal(expected)

    # zlib compression
    csv_bytes = zlib.compress(csv.encode())
    out = pl.read_csv(csv_bytes)
    expected = pl.DataFrame(
        {"a": [1, 2, 3], "b": ["a", "b", "c"], "c": [1.0, 2.0, 3.0]}
    )
    assert out.frame_equal(expected)

    # no compression
    f = io.BytesIO(b"a, b\n1,2\n")
    out = pl.read_csv(f)
    expected = pl.DataFrame({"a": [1], "b": [2]})
    assert out.frame_equal(expected)


def test_empty_bytes():
    b = b""
    with pytest.raises(ValueError):
        pl.read_csv(b)


def test_pickle():
    a = pl.Series("a", [1, 2])
    b = pickle.dumps(a)
    out = pickle.loads(b)
    assert a.series_equal(out)
    df = pl.DataFrame({"a": [1, 2], "b": ["a", None], "c": [True, False]})
    b = pickle.dumps(df)
    out = pickle.loads(b)
    assert df.frame_equal(out, null_equal=True)


def test_copy():
    df = pl.DataFrame({"a": [1, 2], "b": ["a", None], "c": [True, False]})
    assert copy.copy(df).frame_equal(df, True)
    assert copy.deepcopy(df).frame_equal(df, True)

    a = pl.Series("a", [1, 2])
    assert copy.copy(a).series_equal(a, True)
    assert copy.deepcopy(a).series_equal(a, True)


def test_to_json():
    # tests if it runs if no arg given
    df = pl.DataFrame({"a": [1, 2, 3]})
    assert (
        df.to_json() == '{"columns":[{"name":"a","datatype":"Int64","values":[1,2,3]}]}'
    )


def test_ipc_schema():
    df = pl.DataFrame({"a": [1, 2], "b": ["a", None], "c": [True, False]})
    f = io.BytesIO()
    df.to_ipc(f)
    f.seek(0)

    assert pl.read_ipc_schema(f) == {"a": pl.Int64, "b": pl.Utf8, "c": pl.Boolean}


def test_categorical_round_trip():
    df = pl.DataFrame({"ints": [1, 2, 3], "cat": ["a", "b", "c"]})
    df = df.with_column(pl.col("cat").cast(pl.Categorical))

    tbl = df.to_arrow()
    assert "dictionary" in str(tbl["cat"].type)

    df = pl.from_arrow(tbl)
    assert df.dtypes == [pl.Int64, pl.Categorical]


def test_csq_quote_char():
    rolling_stones = """
    linenum,last_name,first_name
    1,Jagger,Mick
    2,O"Brian,Mary
    3,Richards,Keith
    4,L"Etoile,Bennet
    5,Watts,Charlie
    6,Smith,D"Shawn
    7,Wyman,Bill
    8,Woods,Ron
    9,Jones,Brian
    """

    assert pl.read_csv(rolling_stones.encode(), quote_char=None).shape == (9, 3)


def test_date_list_fmt():
    df = pl.DataFrame(
        {
            "mydate": ["2020-01-01", "2020-01-02", "2020-01-05", "2020-01-05"],
            "index": [1, 2, 5, 5],
        }
    )

    df = df.with_column(pl.col("mydate").str.strptime(pl.Date, "%Y-%m-%d"))
    assert (
        str(df.groupby("index", maintain_order=True).agg(pl.col("mydate"))["mydate"])
        == """shape: (3,)
Series: 'mydate' [list]
[
	[2020-01-01]
	[2020-01-02]
	[2020-01-05, 2020-01-05]
]"""
    )


def test_csv_empty_quotes_char():
    # panicked in: https://github.com/pola-rs/polars/issues/1622
    pl.read_csv(b"a,b,c,d\nA1,B1,C1,1\nA2,B2,C2,2\n", quote_char="")


def test_ignore_parse_dates():
    csv = """a,b,c
1,i,16200126
2,j,16250130
3,k,17220012
4,l,17290009""".encode()

    headers = ["a", "b", "c"]
    dtypes = {k: pl.Utf8 for k in headers}  # Forces Utf8 type for every column
    df = pl.read_csv(csv, columns=headers, dtype=dtypes)
    assert df.dtypes == [pl.Utf8, pl.Utf8, pl.Utf8]


def test_scan_csv():
    df = pl.scan_csv(Path(__file__).parent / "files" / "small.csv")
    assert df.collect().shape == (4, 3)


def test_scan_parquet():
    df = pl.scan_parquet(Path(__file__).parent / "files" / "small.parquet")
    assert df.collect().shape == (4, 3)
