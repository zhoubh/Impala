# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#
import pytest
from copy import copy

from tests.beeswax.impala_beeswax import ImpalaBeeswaxException
from tests.common.impala_cluster import ImpalaCluster
from tests.common.impala_test_suite import ImpalaTestSuite
from tests.common.skip import SkipIfNotHdfsMinicluster
from tests.common.test_dimensions import create_single_exec_option_dimension
from tests.common.test_vector import ImpalaTestDimension
from tests.verifiers.metric_verifier import MetricVerifier

# Substrings of the expected error messages when the mem limit is too low
MEM_LIMIT_EXCEEDED_MSG = "Memory limit exceeded"
MEM_LIMIT_TOO_LOW_FOR_RESERVATION = ("minimum memory reservation is greater than memory "
  "available to the query for buffer reservations")
MEM_LIMIT_ERROR_MSGS = [MEM_LIMIT_EXCEEDED_MSG, MEM_LIMIT_TOO_LOW_FOR_RESERVATION]

@SkipIfNotHdfsMinicluster.tuned_for_minicluster
class TestQueryMemLimitScaling(ImpalaTestSuite):
  """Test class to do functional validation of per query memory limits. """
  QUERY = ["select * from lineitem where l_orderkey = -1",
           "select min(l_orderkey) from lineitem",
           "select * from lineitem order by l_orderkey limit 1"]

  # These query take 400mb-1gb if no mem limits are set
  MEM_LIMITS = ["-1", "400m", "150m"]

  @classmethod
  def get_workload(self):
    # Note: this workload doesn't run exhaustively. See IMPALA-3947 before trying to move
    # this test to exhaustive.
    return 'tpch'

  @classmethod
  def add_test_dimensions(cls):
    super(TestQueryMemLimitScaling, cls).add_test_dimensions()
    # add mem_limit as a test dimension.
    new_dimension = ImpalaTestDimension('mem_limit',
                                        *TestQueryMemLimitScaling.MEM_LIMITS)
    cls.ImpalaTestMatrix.add_dimension(new_dimension)
    if cls.exploration_strategy() != 'exhaustive':
      cls.ImpalaTestMatrix.add_constraint(lambda v:\
          v.get_value('table_format').file_format in ['parquet'])

  # Test running with different mem limits to exercise the dynamic memory
  # scaling functionality.
  def test_mem_usage_scaling(self, vector):
    mem_limit = copy(vector.get_value('mem_limit'))
    table_format = vector.get_value('table_format')
    exec_options = copy(vector.get_value('exec_option'))
    exec_options['mem_limit'] = mem_limit
    for query in self.QUERY:
      self.execute_query(query, exec_options, table_format=table_format)


class TestExprMemUsage(ImpalaTestSuite):
  @classmethod
  def get_workload(cls):
    # Note: this workload doesn't run exhaustively. See IMPALA-3947 before trying to move
    # this test to exhaustive.
    return 'tpch'

  @classmethod
  def add_test_dimensions(cls):
    super(TestExprMemUsage, cls).add_test_dimensions()
    cls.ImpalaTestMatrix.add_dimension(create_single_exec_option_dimension())
    if cls.exploration_strategy() != 'exhaustive':
      cls.ImpalaTestMatrix.add_constraint(lambda v:\
          v.get_value('table_format').file_format in ['parquet'])

  def test_scanner_mem_usage(self, vector):
    exec_options = vector.get_value('exec_option')
    # This value was picked empircally based on the query.
    exec_options['mem_limit'] = '300m'
    self.execute_query_expect_success(self.client,
      "select count(*) from lineitem where lower(l_comment) = 'hello'", exec_options,
      table_format=vector.get_value('table_format'))

class TestLowMemoryLimits(ImpalaTestSuite):
  '''Super class for the memory limit tests with the TPC-H and TPC-DS queries'''

  def low_memory_limit_test(self, vector, tpch_query, limit):
    mem = vector.get_value('mem_limit')
    # Mem consumption can be +-30MBs, depending on how many scanner threads are
    # running. Adding this extra mem in order to reduce false negatives in the tests.
    limit = limit + 30

    # If memory limit larger than the minimum threshold, then it is not expected to fail.
    expects_error = mem < limit
    new_vector = copy(vector)
    exec_options = new_vector.get_value('exec_option')
    exec_options['mem_limit'] = str(mem) + "m"

    # Reduce the page size to better exercise page boundary logic.
    exec_options['default_spillable_buffer_size'] = "256k"
    try:
      self.run_test_case(tpch_query, new_vector)
    except ImpalaBeeswaxException as e:
      if not expects_error: raise
      found_expected_error = False
      for error_msg in MEM_LIMIT_ERROR_MSGS:
        if error_msg in str(e): found_expected_error = True
      assert found_expected_error, str(e)


@SkipIfNotHdfsMinicluster.tuned_for_minicluster
class TestTpchMemLimitError(TestLowMemoryLimits):
  # The mem limits that will be used.
  MEM_IN_MB = [20, 50, 80, 130, 160, 200, 400]

  # Different values of mem limits and minimum mem limit (in MBs) each query is expected
  # to run without problem. These were determined using the query_runtime_info.json file
  # produced by the stress test (i.e. concurrent_select.py) and extracted with
  # tests/stress/extract_min_mem.py
  MIN_MEM_FOR_TPCH = {'Q1': 55, 'Q2': 89, 'Q3': 80, 'Q4': 70, 'Q5': 99,
                      'Q6': 48, 'Q7': 127, 'Q8': 111, 'Q9': 189, 'Q10': 108,
                      'Q11': 76, 'Q12': 70, 'Q13': 71, 'Q14': 57, 'Q15': 83,
                      'Q16': 71, 'Q17': 73, 'Q18': 153, 'Q19': 54, 'Q20': 128,
                      'Q21': 147, 'Q22': 57}

  @classmethod
  def get_workload(self):
    # Note: this workload doesn't run exhaustively. See IMPALA-3947 before trying to move
    # this test to exhaustive.
    return 'tpch'

  @classmethod
  def add_test_dimensions(cls):
    super(TestTpchMemLimitError, cls).add_test_dimensions()

    cls.ImpalaTestMatrix.add_dimension(
      ImpalaTestDimension('mem_limit', *TestTpchMemLimitError.MEM_IN_MB))

    cls.ImpalaTestMatrix.add_constraint(lambda v:\
        v.get_value('table_format').file_format in ['parquet'])

  def test_low_mem_limit_q1(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q1', self.MIN_MEM_FOR_TPCH['Q1'])

  def test_low_mem_limit_q2(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q2', self.MIN_MEM_FOR_TPCH['Q2'])

  def test_low_mem_limit_q3(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q3', self.MIN_MEM_FOR_TPCH['Q3'])

  def test_low_mem_limit_q4(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q4', self.MIN_MEM_FOR_TPCH['Q4'])

  def test_low_mem_limit_q5(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q5', self.MIN_MEM_FOR_TPCH['Q5'])

  def test_low_mem_limit_q6(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q6', self.MIN_MEM_FOR_TPCH['Q6'])

  def test_low_mem_limit_q7(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q7', self.MIN_MEM_FOR_TPCH['Q7'])

  def test_low_mem_limit_q8(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q8', self.MIN_MEM_FOR_TPCH['Q8'])

  def test_low_mem_limit_q9(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q9', self.MIN_MEM_FOR_TPCH['Q9'])

  def test_low_mem_limit_q10(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q10', self.MIN_MEM_FOR_TPCH['Q10'])

  def test_low_mem_limit_q11(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q11', self.MIN_MEM_FOR_TPCH['Q11'])

  def test_low_mem_limit_q12(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q12', self.MIN_MEM_FOR_TPCH['Q12'])

  def test_low_mem_limit_q13(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q13', self.MIN_MEM_FOR_TPCH['Q13'])

  def test_low_mem_limit_q14(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q14', self.MIN_MEM_FOR_TPCH['Q14'])

  def test_low_mem_limit_q15(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q15', self.MIN_MEM_FOR_TPCH['Q15'])

  def test_low_mem_limit_q16(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q16', self.MIN_MEM_FOR_TPCH['Q16'])

  def test_low_mem_limit_q17(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q17', self.MIN_MEM_FOR_TPCH['Q17'])

  def test_low_mem_limit_q18(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q18', self.MIN_MEM_FOR_TPCH['Q18'])

  def test_low_mem_limit_q19(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q19', self.MIN_MEM_FOR_TPCH['Q19'])

  def test_low_mem_limit_q20(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q20', self.MIN_MEM_FOR_TPCH['Q20'])

  def test_low_mem_limit_q21(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q21', self.MIN_MEM_FOR_TPCH['Q21'])

  def test_low_mem_limit_q22(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q22', self.MIN_MEM_FOR_TPCH['Q22'])

  @pytest.mark.execute_serially
  def test_low_mem_limit_no_fragments(self, vector):
    self.low_memory_limit_test(vector, 'tpch-q14', self.MIN_MEM_FOR_TPCH['Q14'])
    self.low_memory_limit_test(vector, 'tpch-q18', self.MIN_MEM_FOR_TPCH['Q18'])
    self.low_memory_limit_test(vector, 'tpch-q20', self.MIN_MEM_FOR_TPCH['Q20'])
    for impalad in ImpalaCluster().impalads:
      verifier = MetricVerifier(impalad.service)
      verifier.wait_for_metric("impala-server.num-fragments-in-flight", 0)

@SkipIfNotHdfsMinicluster.tuned_for_minicluster
class TestTpchPrimitivesMemLimitError(TestLowMemoryLimits):
  """
  Memory usage tests using targeted-perf queries to exercise specific operators.
  """

  # The mem limits that will be used.
  MEM_IN_MB = [20, 100, 120, 200]

  # Different values of mem limits and minimum mem limit (in MBs) each query is expected
  # to run without problem. Determined by manual binary search.
  MIN_MEM = { 'primitive_broadcast_join_3': 115, 'primitive_groupby_bigint_highndv': 110,
              'primitive_orderby_all': 120}

  @classmethod
  def get_workload(self):
    # Note: this workload doesn't run exhaustively. See IMPALA-3947 before trying to move
    # this test to exhaustive.
    return 'targeted-perf'

  @classmethod
  def add_test_dimensions(cls):
    super(TestTpchPrimitivesMemLimitError, cls).add_test_dimensions()

    cls.ImpalaTestMatrix.add_dimension(
      ImpalaTestDimension('mem_limit', *cls.MEM_IN_MB))

    cls.ImpalaTestMatrix.add_constraint(lambda v:\
        v.get_value('table_format').file_format in ['parquet'])

  def run_primitive_query(self, vector, query_name):
    self.low_memory_limit_test(vector, query_name, self.MIN_MEM[query_name])

  def test_low_mem_limit_broadcast_join_3(self, vector):
    """Test hash join memory requirements."""
    self.run_primitive_query(vector, 'primitive_broadcast_join_3')

  def test_low_mem_limit_groupby_bigint_highndv(self, vector):
    """Test grouping aggregation memory requirements."""
    self.run_primitive_query(vector, 'primitive_groupby_bigint_highndv')

  def test_low_mem_limit_orderby_all(self, vector):
    """Test sort and analytic memory requirements."""
    self.run_primitive_query(vector, 'primitive_orderby_all')


@SkipIfNotHdfsMinicluster.tuned_for_minicluster
class TestTpcdsMemLimitError(TestLowMemoryLimits):
  # The mem limits that will be used.
  MEM_IN_MB = [20, 100, 116, 150]

  # Different values of mem limits and minimum mem limit (in MBs) each query is expected
  # to run without problem. Those values were determined by manual testing.
  MIN_MEM_FOR_TPCDS = { 'q53' : 116}

  @classmethod
  def get_workload(self):
    # Note: this workload doesn't run exhaustively. See IMPALA-3947 before trying to move
    # this test to exhaustive.
    return 'tpcds'

  @classmethod
  def add_test_dimensions(cls):
    super(TestTpcdsMemLimitError, cls).add_test_dimensions()

    cls.ImpalaTestMatrix.add_dimension(
      ImpalaTestDimension('mem_limit', *TestTpcdsMemLimitError.MEM_IN_MB))

    cls.ImpalaTestMatrix.add_constraint(lambda v:\
        v.get_value('table_format').file_format in ['parquet'])

  def test_low_mem_limit_q53(self, vector):
    self.low_memory_limit_test(vector, 'tpcds-q53', self.MIN_MEM_FOR_TPCDS['q53'])
